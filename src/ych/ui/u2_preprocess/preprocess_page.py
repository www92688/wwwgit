# 预处理工作台（U2）：素材树 + 处理项面板 + 框选画布 + 开始按钮
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ych.ui.u2_preprocess.asset_tree import AssetTree
from ych.ui.u2_preprocess.box_select_canvas import BoxSelectCanvas
from ych.ui.u2_preprocess.option_panel import OptionPanel
from ych.ui.u6_common.context_actions import copy_to_clipboard, reveal_in_file_manager
from ych.ui.u6_common.empty_state import attach_empty_state
from ych.ui.u6_common.toast import Toast


class _SchedulerLike(Protocol):
    def submit(self, payload: object) -> str: ...

    task_state: QObject
    task_progress: QObject


class PreprocessPage(QWidget):
    """勾素材 → 选项（可手动框选）→ 开始，三步约束。"""

    submitted = Signal(int)      # 提交条数

    def __init__(
        self,
        scheduler: _SchedulerLike | None = None,
        frame_loader: Callable[[str], str] | None = None,   # 素材→预览帧图
        config: Any | None = None,                          # 选项记忆
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scheduler = scheduler
        self._frame_loader = frame_loader
        self._preview_worker: Any | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ---- 步骤引导 ----
        from ych.ui.u6_common.step_hint import StepHint

        self._step_hint = StepHint(self._step_texts())
        root.addWidget(self._step_hint)

        split = QSplitter()
        self.asset_tree = AssetTree()
        self._empty = attach_empty_state(
            self.asset_tree, self.tr("暂无素材"),
            self.tr("先到「采集工作台」下载素材，\n或把视频文件放入工作目录"),
        )
        btn_all = QPushButton(self.tr("全选"))
        btn_all.setObjectName("secondaryBtn")
        btn_none = QPushButton(self.tr("全不选"))
        btn_none.setObjectName("secondaryBtn")
        self.btn_all = btn_all
        self.btn_none = btn_none
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.asset_tree, 1)
        row_btns = QHBoxLayout()
        row_btns.addWidget(btn_all)
        row_btns.addWidget(btn_none)
        left_layout.addLayout(row_btns)
        btn_all.clicked.connect(lambda: self.asset_tree.select_all(True))
        btn_none.clicked.connect(lambda: self.asset_tree.select_all(False))
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.option_panel = OptionPanel(config=config)
        right_layout.addWidget(self.option_panel)
        self.canvas = BoxSelectCanvas()
        right_layout.addWidget(self.canvas, 1)
        split.addWidget(right)
        root.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.btn_preview = QPushButton(self.tr("预览框选帧"))
        self.btn_preview.setObjectName("secondaryBtn")
        self.btn_preview.clicked.connect(self._on_preview)
        self.btn_start = QPushButton(self.tr("开始处理"))
        self.btn_start.clicked.connect(self._on_start)
        bottom.addWidget(self.btn_preview)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_start)
        root.addLayout(bottom)

        # 手动模式：画布新区域注册到面板上下文；清空按钮联动画布
        self.canvas.regions_changed.connect(self._sync_manual_regions)
        self.option_panel.clear_requested.connect(self.canvas.clear_regions)
        # 勾选数量反馈到开始按钮
        self.asset_tree.selection_changed.connect(self._refresh_start_btn)
        self._refresh_start_btn()
        # 双击叶子素材 → 画布预览该素材帧；右键定位/复制路径
        self.asset_tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self.asset_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu,
        )
        self.asset_tree.customContextMenuRequested.connect(
            self._show_tree_menu,
        )

    # ---- 数据 ----
    def _step_texts(self) -> list[str]:
        """步骤条文案（语言切换时重取 tr）。"""
        return [
            self.tr("左侧勾选素材"),
            self.tr("右侧选择处理项（手动模式可预览后框选区域）"),
            self.tr("「开始处理」提交"),
        ]

    def set_assets(self, rows: list[Any]) -> None:
        self.asset_tree.set_assets(rows)
        refresh_empty = getattr(self.asset_tree, "_refresh_empty_state", None)
        if refresh_empty is not None:
            refresh_empty()

    def _refresh_start_btn(self) -> None:
        n = len(self.asset_tree.checked_files())
        self.btn_start.setText(
            self.tr("开始处理（{}）").format(n) if n else self.tr("开始处理"),
        )
        self.btn_start.setEnabled(n > 0)

    def load_frame_image(self, image_path: str) -> None:
        self.canvas.set_image(image_path)

    def _sync_manual_regions(self) -> None:
        """画布区域变化钩子（框选结果在提交时读取）。"""

    # ---- 提交 ----
    @staticmethod
    def _regions_to_dict(regions: Any) -> dict[str, object] | None:
        """ManualRegions → revive_ops 可还原的 dict（每条素材独立拷贝）。"""
        if regions is None:
            return None
        return {
            "rects": [
                {"x": b.x, "y": b.y, "w": b.w, "h": b.h} for b in regions.rects
            ],
            "time_start_s": regions.time_start_s,
            "time_end_s": regions.time_end_s,
        }

    def build_payload(self) -> Any:
        from ych.core.m2_preprocess.ops import (
            make_preprocess_payload,
            revive_ops,
        )

        srcs = self.asset_tree.checked_files()
        if not srcs:
            return None
        boxes = getattr(self.canvas, "_boxes", [])
        manual = self.option_panel.manual_from_boxes(boxes) if boxes else None
        ops_template = self.option_panel.to_ops(manual)
        wm_regions = self._regions_to_dict(ops_template.watermark_regions)
        sub_regions = self._regions_to_dict(ops_template.subtitle_regions)
        crop = ops_template.crop_rect
        items = []
        for src in srcs:
            # 每条独立还原 ops（regions/crop 经 dict 拷贝，不共享可变对象）
            ops = revive_ops({
                "remove_watermark_mode": ops_template.remove_watermark_mode,
                "watermark_regions": wm_regions,
                "remove_subtitle_mode": ops_template.remove_subtitle_mode,
                "subtitle_regions": sub_regions,
                "crop_rect": ({"x": crop.x, "y": crop.y,
                               "w": crop.w, "h": crop.h} if crop else None),
                "aspect_target": ops_template.aspect_target,
                "aspect_strategy": ops_template.aspect_strategy,
                "strip_audio": ops_template.strip_audio,
            })
            items.append((src, ops))
        return make_preprocess_payload([
            (s, ops) for s, ops in items
        ])

    def _on_start(self) -> None:
        payload: object | None = self.build_payload()
        if payload is None:
            Toast.show_message(self, self.tr("请先在左侧勾选要处理的素材"))
            return
        if self._manual_without_regions():
            Toast.show_message(
                self,
                self.tr(
                    "「手动框选」需要先「预览框选帧」并在画布上框选区域；"
                    "若不需要去水印/去字幕，请改为「关闭」或「自动检测」",
                ),
                error=True,
            )
            return
        if self._scheduler is None:
            return
        data: dict[str, object] = getattr(payload, "data", {})
        raw_items = data.get("items") or []
        items: list[object] = list(raw_items) if isinstance(raw_items, list) else []
        self._scheduler.submit(payload)
        self.submitted.emit(len(items))

    def _manual_without_regions(self) -> bool:
        """手动框选模式但画布没有任何框选区域——提交等于该维度空转。"""
        ops = self.option_panel.to_ops(None)
        boxes = getattr(self.canvas, "_boxes", [])
        if boxes:
            return False
        return (ops.remove_watermark_mode == "manual"
                or ops.remove_subtitle_mode == "manual")

    # ---- 预览框选帧 ----
    def _on_preview(self) -> None:
        """取第一个勾选素材的中间帧显示到画布，供手动框选。"""
        srcs = self.asset_tree.checked_files()
        if not srcs:
            Toast.show_message(
                self, self.tr("请先在左侧勾选素材，再预览框选帧"),
            )
            return
        self._load_frame_async(srcs[0])

    def _load_frame_async(self, src: str) -> None:
        """后台抽帧 → 画布显示；进行中时提示稍候，不静默丢弃。"""
        from ych.ui.u6_common.llm_worker import LlmWorker

        if self._frame_loader is None:
            return
        if self._preview_worker is not None:
            Toast.show_message(self, self.tr("预览帧正在加载，请稍候…"))
            return
        self.btn_preview.setEnabled(False)
        loader = self._frame_loader
        worker = LlmWorker(lambda: loader(src))
        self._preview_worker = worker
        worker.done.connect(self._on_preview_done)      # 绑定方法→回 UI 线程
        worker.failed.connect(self._on_preview_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_tree_double_click(self, item: Any, _column: int) -> None:
        """双击叶子素材：画布预览该素材帧（分类/关键词节点忽略）。"""
        if item.childCount() > 0:
            return
        src = item.data(0, Qt.ItemDataRole.UserRole)
        if src:
            self._load_frame_async(str(src))

    def _show_tree_menu(self, pos: Any) -> None:
        """叶子右键：定位文件 / 复制路径。"""
        item = self.asset_tree.itemAt(pos)
        if item is None or item.childCount() > 0:
            return
        src = item.data(0, Qt.ItemDataRole.UserRole)
        if not src:
            return
        menu = QMenu(self)
        act_reveal = menu.addAction(self.tr("打开所在文件夹"))
        act_copy = menu.addAction(self.tr("复制路径"))
        chosen = menu.exec(self.asset_tree.viewport().mapToGlobal(pos))
        if chosen is act_reveal:
            reveal_in_file_manager(str(src))
        elif chosen is act_copy:
            copy_to_clipboard(str(src))

    def _restore_preview(self) -> None:
        self.btn_preview.setEnabled(True)
        self._preview_worker = None

    def _on_preview_done(self, path: object) -> None:
        self._restore_preview()
        self.load_frame_image(str(path))

    def _on_preview_failed(self, msg: str) -> None:
        self._restore_preview()
        Toast.show_message(self, self.tr("抽帧失败：{msg}").format(msg=msg),
                           error=True)

    def retranslate(self) -> None:
        """语言切换：静态文案重翻译；动态按钮经刷新方法重算。"""
        self._step_hint.set_steps(self._step_texts())
        self._empty.set_texts(
            self.tr("暂无素材"),
            self.tr("先到「采集工作台」下载素材，\n或把视频文件放入工作目录"),
        )
        self.btn_all.setText(self.tr("全选"))
        self.btn_none.setText(self.tr("全不选"))
        self.btn_preview.setText(self.tr("预览框选帧"))
        self.asset_tree.retranslate()
        self.option_panel.retranslate()
        self.canvas.retranslate()
        self._refresh_start_btn()
