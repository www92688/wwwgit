# 去重工作台（U3）：素材勾选 / 三档方案卡片 / 自定义编辑 / 报告视图
from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ych.core.m3_dedup.techniques.registry import TechniqueRegistry
from ych.ui.u3_dedup.report_view import ReportView
from ych.ui.u3_dedup.scheme_editor import SchemeEditor


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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry or make_registry()
        self._schemes = scheme_manager

        root = QVBoxLayout(self)
        split = QHBoxLayout()

        # ---- 左：素材勾选列表 ----
        left_box = QVBoxLayout()
        left_box.addWidget(QLabel(self.tr("待去重素材")))
        self.asset_list = QListWidget()
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
        for pid, label in (("light", "轻度"), ("mid", "中度"),
                           ("heavy", "重度")):
            radio = QRadioButton(label + "（推荐档）")
            radio.setProperty("preset_id", pid)
            self.radio_group.addButton(radio)
            radio.setChecked(pid == "mid")
            right_box.addWidget(radio)
            self._preset_radios[pid] = radio
        self.editor = SchemeEditor(self._registry)
        right_box.addWidget(self.editor, 1)

        row_btns = QHBoxLayout()
        btn_apply_preset = QPushButton("套用预设")
        btn_apply_preset.setObjectName("secondaryBtn")
        btn_apply_preset.clicked.connect(self._apply_preset)
        btn_to_custom = QPushButton("预设→自定义微调")
        btn_to_custom.setObjectName("secondaryBtn")
        btn_to_custom.clicked.connect(self._to_custom)
        row_btns.addWidget(btn_apply_preset)
        row_btns.addWidget(btn_to_custom)
        right_box.addLayout(row_btns)

        row_analyze = QHBoxLayout()
        btn_analyze = QPushButton(self.tr("分析重复度"))
        btn_analyze.clicked.connect(self._emit_analyze)
        btn_start = QPushButton(self.tr("开始去重"))
        btn_start.clicked.connect(self._emit_dedup)
        row_analyze.addWidget(btn_analyze)
        row_analyze.addWidget(btn_start)
        right_box.addLayout(row_analyze)
        split.addLayout(right_box, 1)
        root.addLayout(split, 1)

        # ---- 底：报告 ----
        self.report_view = ReportView()
        root.addWidget(self.report_view, 1)

        # 初始套用默认档（中度），保证编辑器非空
        self._apply_preset()

    # ---- 数据 ----
    def set_assets(self, paths: list[str]) -> None:
        self.asset_list.clear()
        for p in paths:
            item = QListWidgetItem(p)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.asset_list.addItem(item)

    def checked_paths(self) -> list[str]:
        out = []
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.text())
        return out

    def render_report(self, report, before_pct=None, after_pct=None):   # type: ignore[no-untyped-def]
        self.report_view.show_report(report, before_pct, after_pct)
        if report is not None and self._schemes is not None:
            recommended = self._schemes.recommend(
                float(report.overall_score) / 100.0 if report.overall_score > 1.5
                else float(report.overall_score),
            )
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

    def _emit_dedup(self) -> None:
        srcs = self.checked_paths()
        if srcs:
            self.dedup_requested.emit(srcs, self.current_params())


def make_registry() -> TechniqueRegistry:
    from ych.core.m3_dedup.techniques.registry import make_default_registry

    return make_default_registry()


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
