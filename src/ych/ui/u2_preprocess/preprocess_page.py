# 预处理工作台（U2）：素材树 + 处理项面板 + 框选画布 + 开始按钮
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ych.ui.u2_preprocess.asset_tree import AssetTree
from ych.ui.u2_preprocess.box_select_canvas import BoxSelectCanvas
from ych.ui.u2_preprocess.option_panel import OptionPanel
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scheduler = scheduler
        self._frame_loader = frame_loader
        self._preview_worker: Any | None = None
        root = QVBoxLayout(self)

        split = QSplitter()
        self.asset_tree = AssetTree()
        btn_all = QPushButton(self.tr("全选"))
        btn_all.setObjectName("secondaryBtn")
        btn_none = QPushButton(self.tr("全不选"))
        btn_none.setObjectName("secondaryBtn")
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
        self.option_panel = OptionPanel()
        right_layout.addWidget(self.option_panel)
        self.canvas = BoxSelectCanvas()
        right_layout.addWidget(self.canvas, 1)
        split.addWidget(right)
        root.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.btn_preview = QPushButton("预览框选帧")
        self.btn_preview.setObjectName("secondaryBtn")
        self.btn_preview.clicked.connect(self._on_preview)
        btn_start = QPushButton(self.tr("开始处理"))
        btn_start.clicked.connect(self._on_start)
        bottom.addWidget(self.btn_preview)
        bottom.addStretch(1)
        bottom.addWidget(btn_start)
        root.addLayout(bottom)

        # 手动模式：画布新区域注册到面板上下文；清空按钮联动画布
        self.canvas.regions_changed.connect(self._sync_manual_regions)
        self.option_panel.clear_requested.connect(self.canvas.clear_regions)

    # ---- 数据 ----
    def set_assets(self, rows: list[Any]) -> None:
        self.asset_tree.set_assets(rows)

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
            Toast.show_message(self, "请先在左侧勾选要处理的素材")
            return
        if self._scheduler is None:
            return
        data: dict[str, object] = getattr(payload, "data", {})
        raw_items = data.get("items") or []
        items: list[object] = list(raw_items) if isinstance(raw_items, list) else []
        self._scheduler.submit(payload)
        self.submitted.emit(len(items))

    # ---- 预览框选帧 ----
    def _on_preview(self) -> None:
        """取第一个勾选素材的中间帧显示到画布，供手动框选。"""
        from ych.ui.u6_common.llm_worker import LlmWorker

        if self._frame_loader is None or self._preview_worker is not None:
            return
        srcs = self.asset_tree.checked_files()
        if not srcs:
            Toast.show_message(self, "请先在左侧勾选素材，再预览框选帧")
            return
        self.btn_preview.setEnabled(False)
        src = srcs[0]
        loader = self._frame_loader
        assert loader is not None
        worker = LlmWorker(lambda: loader(src))
        self._preview_worker = worker
        worker.done.connect(self._on_preview_done)      # 绑定方法→回 UI 线程
        worker.failed.connect(self._on_preview_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _restore_preview(self) -> None:
        self.btn_preview.setEnabled(True)
        self._preview_worker = None

    def _on_preview_done(self, path: object) -> None:
        self._restore_preview()
        self.load_frame_image(str(path))

    def _on_preview_failed(self, msg: str) -> None:
        self._restore_preview()
        Toast.show_message(self, f"抽帧失败：{msg}", error=True)
