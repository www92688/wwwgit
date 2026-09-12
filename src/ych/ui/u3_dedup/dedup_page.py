# 去重工作台（U3）：素材勾选 / 三档方案卡片 / 自定义编辑 / 报告视图
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ych.core.m3_dedup.techniques.registry import TechniqueRegistry
from ych.ui.u3_dedup.report_view import ReportView
from ych.ui.u3_dedup.scheme_editor import SchemeEditor
from ych.ui.u6_common.context_actions import (
    copy_to_clipboard,
    reveal_in_file_manager,
)
from ych.ui.u6_common.empty_state import attach_empty_state
from ych.ui.u6_common.toast import Toast


class _SchemeManagerLike(Protocol):
    def recommend(self, score: float) -> str: ...

    def instantiate(self, preset_id: str, seed: int | None = None) -> list[dict[str, object]]: ...

    def preset_to_custom(
        self, preset_id: str, seed: int | None = None,
    ) -> list[dict[str, object]]: ...

    def save_custom(self, name: str, config: list[dict[str, object]]) -> int: ...

    def load_custom(self, name: str) -> list[dict[str, object]] | None: ...


class DedupPage(QWidget):
    """选素材 → 选方案 → 开始，三步约束。"""

    analyze_requested = Signal(list)          # 选中的 srcs（compare 任务）
    dedup_requested = Signal(list, list)      # (srcs, technique_params)

    def __init__(
        self,
        registry: TechniqueRegistry | None = None,
        scheme_manager: _SchemeManagerLike | None = None,
        config: Any | None = None,          # 档位记忆（可选）
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry or make_registry()
        self._schemes = scheme_manager
        self._config = config

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ---- 步骤引导 ----
        from ych.ui.u6_common.step_hint import StepHint

        root.addWidget(StepHint([
            self.tr("左侧勾选素材"),
            self.tr("「分析重复度」后选方案（轻/中/重度或自定义）"),
            self.tr("「开始去重」提交"),
        ]))

        split = QHBoxLayout()

        # ---- 左：素材勾选列表 ----
        left_box = QVBoxLayout()
        left_box.addWidget(QLabel(self.tr("待去重素材")))
        self.asset_list = QListWidget()
        attach_empty_state(
            self.asset_list, self.tr("暂无素材"),
            self.tr("先到「采集工作台」下载素材，\n或把视频文件放入工作目录"),
        )
        left_box.addWidget(self.asset_list, 1)
        btn_all = QPushButton(self.tr("全选"))
        btn_all.setObjectName("secondaryBtn")
        btn_all.clicked.connect(self._select_all)
        left_box.addWidget(btn_all)
        split.addLayout(left_box, 1)

        # ---- 右：方案区 ----
        right_box = QVBoxLayout()
        right_box.addWidget(QLabel(self.tr("去重方案")))
        self.radio_group = QButtonGroup(self)
        self._preset_radios: dict[str, QRadioButton] = {}
        preset_descs = {
            "light": self.tr("保守调整：轻度镜像/微裁切/轻调色，画质损失最小"),
            "mid": self.tr("多手法组合：推荐日常使用，重复度下降明显"),
            "heavy": self.tr("强力规避：全部手法叠加，适合重复度很高的素材"),
        }
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        for pid, label in (("light", "轻度"), ("mid", "中度"),
                           ("heavy", "重度")):
            card = QWidget()
            card.setObjectName("presetCard")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(10, 8, 10, 8)
            card_lay.setSpacing(4)
            radio = QRadioButton(label)
            radio.setProperty("preset_id", pid)
            self.radio_group.addButton(radio)
            radio.setChecked(pid == "mid")
            # 点击档位立即套用对应预设，而非等"套用预设"按钮；并记忆档位
            radio.toggled.connect(
                lambda on, p=pid: self._apply_preset() if on else None,
            )
            radio.toggled.connect(
                lambda on, p=pid: self._save_preset(p) if on else None,
            )
            radio.toggled.connect(
                lambda on, c=card: _set_card_checked(c, on),
            )
            desc = QLabel(preset_descs[pid])
            desc.setObjectName("muted")
            desc.setWordWrap(True)
            card_lay.addWidget(radio)
            card_lay.addWidget(desc)
            _set_card_checked(card, radio.isChecked())
            cards_row.addWidget(card, 1)
            self._preset_radios[pid] = radio
        right_box.addLayout(cards_row)
        self.editor = SchemeEditor(self._registry)
        right_box.addWidget(self.editor, 1)

        row_btns = QHBoxLayout()
        btn_apply_preset = QPushButton(self.tr("套用预设"))
        btn_apply_preset.setObjectName("secondaryBtn")
        btn_apply_preset.clicked.connect(self._apply_preset)
        btn_to_custom = QPushButton(self.tr("预设→自定义微调"))
        btn_to_custom.setObjectName("secondaryBtn")
        btn_to_custom.clicked.connect(self._to_custom)
        row_btns.addWidget(btn_apply_preset)
        row_btns.addWidget(btn_to_custom)
        right_box.addLayout(row_btns)

        row_analyze = QHBoxLayout()
        self.btn_analyze = QPushButton(self.tr("分析重复度"))
        self.btn_analyze.clicked.connect(self._emit_analyze)
        self.btn_start = QPushButton(self.tr("开始去重"))
        self.btn_start.clicked.connect(self._emit_dedup)
        row_analyze.addWidget(self.btn_analyze)
        row_analyze.addWidget(self.btn_start)
        right_box.addLayout(row_analyze)
        split.addLayout(right_box, 1)
        root.addLayout(split, 1)

        # ---- 底：报告 ----
        self.report_view = ReportView()
        root.addWidget(self.report_view, 1)

        # 勾选数量反馈到开始按钮
        self.asset_list.itemChanged.connect(lambda _item: self._refresh_btns())
        # 右键：定位/复制/系统播放器打开
        self.asset_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu,
        )
        self.asset_list.customContextMenuRequested.connect(
            self._show_asset_menu,
        )
        # 初始套用默认档（中度），保证编辑器非空；有记忆则恢复上次档位
        self._apply_preset()
        if config is not None:
            saved = self._saved_preset()
            if saved and saved in self._preset_radios:
                self._preset_radios[saved].setChecked(True)
        self._refresh_btns()

    def _saved_preset(self) -> str:
        if self._config is None:
            return ""
        try:
            saved = self._config.get("dedup_preset")
        except Exception:
            return ""
        return str(saved) if isinstance(saved, str) else ""

    def _save_preset(self, pid: str) -> None:
        if self._config is not None:
            self._config.set("dedup_preset", pid)

    def _refresh_btns(self) -> None:
        n = len(self.checked_paths())       # 实际勾选数
        self.btn_analyze.setText(
            self.tr("分析重复度（{}）").format(n) if n else self.tr("分析重复度"),
        )
        self.btn_start.setText(
            self.tr("开始去重（{}）").format(n) if n else self.tr("开始去重"),
        )
        self.btn_analyze.setEnabled(n > 0)
        self.btn_start.setEnabled(n > 0)

    # ---- 数据 ----
    def set_assets(self, paths: list[str]) -> None:
        self.asset_list.blockSignals(True)
        self.asset_list.clear()
        for p in paths:
            # 只显示文件名，完整路径放 Tooltip（长路径更易读）
            item = QListWidgetItem(Path(p).name)
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.asset_list.addItem(item)
        self.asset_list.blockSignals(False)
        refresh_empty = getattr(self.asset_list, "_refresh_empty_state", None)
        if refresh_empty is not None:
            refresh_empty()
        self._refresh_btns()

    def checked_paths(self) -> list[str]:
        out = []
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                p = item.data(Qt.ItemDataRole.UserRole)
                out.append(str(p) if p else item.text())
        return out

    def _show_asset_menu(self, pos: Any) -> None:
        item = self.asset_list.itemAt(pos)
        if item is None:
            return
        path = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        menu = QMenu(self)
        act_reveal = menu.addAction(self.tr("打开所在文件夹"))
        act_copy = menu.addAction(self.tr("复制路径"))
        act_play = menu.addAction(self.tr("用系统播放器打开"))
        chosen = menu.exec(self.asset_list.viewport().mapToGlobal(pos))
        if chosen is act_reveal:
            reveal_in_file_manager(path)
        elif chosen is act_copy:
            copy_to_clipboard(path)
        elif chosen is act_play:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def render_report(self, report, before_pct=None, after_pct=None):   # type: ignore[no-untyped-def]
        self.report_view.show_report(report, before_pct, after_pct)
        if report is not None and self._schemes is not None:
            # ReportBuilder 落库的 overall_score 恒为 0~100 百分数
            recommended = self._schemes.recommend(float(report.overall_score) / 100.0)
            self.mark_recommended(recommended)

    def mark_recommended(self, preset_id: str) -> None:
        """推荐档徽标：非推荐档标题去掉徽标字样。"""
        for pid, radio in self._preset_radios.items():
            base = {"light": "轻度", "mid": "中度", "heavy": "重度"}[pid]
            badge = "（推荐档）" if pid == preset_id else ""
            radio.setText(base + badge)

    def current_params(self) -> list[dict[str, object]]:
        return self.editor.collect()

    # ---- 槽 ----
    def _select_all(self) -> None:
        state = Qt.CheckState.Checked
        for i in range(self.asset_list.count()):
            self.asset_list.item(i).setCheckState(state)

    def _current_preset(self) -> str:
        button = self.radio_group.checkedButton()
        return str(button.property("preset_id")) if button is not None else "mid"

    def _apply_preset(self) -> None:
        pid = self._current_preset()
        params = (self._schemes.instantiate(str(pid)) if self._schemes
                  else default_params(str(pid)))
        self.editor.set_items(params)

    def _to_custom(self) -> None:
        pid = self._current_preset()
        params = (self._schemes.preset_to_custom(str(pid)) if self._schemes
                  else default_params(str(pid)))
        self.editor.set_items(params)

    def _emit_analyze(self) -> None:
        srcs = self.checked_paths()
        if srcs:
            self.analyze_requested.emit(srcs)
        else:
            Toast.show_message(self, "请先在左侧勾选素材，再分析重复度")

    def _emit_dedup(self) -> None:
        srcs = self.checked_paths()
        if srcs:
            self.dedup_requested.emit(srcs, self.current_params())
        else:
            Toast.show_message(self, "请先在左侧勾选素材，再开始去重")


def make_registry() -> TechniqueRegistry:
    from ych.core.m3_dedup.techniques.registry import make_default_registry

    return make_default_registry()


def _set_card_checked(card: QWidget, checked: bool) -> None:
    """卡片选中态 → 动态属性 + 重刷 QSS（presetCard[checked] 规则）。"""
    card.setProperty("checked", checked)
    style = card.style()
    style.unpolish(card)
    style.polish(card)


def default_params(preset_id: str) -> list[dict[str, object]]:
    """无 SchemeManager 实例时的静态参数兜底（取各区间中值）。"""
    from ych.core.m3_dedup.scheme_manager import PRESETS

    preset = PRESETS.get(preset_id)
    if preset is None:
        return []
    out: list[dict[str, object]] = []
    for tid, param_ranges in preset.techniques:
        params: dict[str, object] = {}
        for key, (lo, hi) in param_ranges.items():
            params[key] = round((float(lo) + float(hi)) / 2.0, 3)
        out.append({"id": tid, "params": params})
    return out
