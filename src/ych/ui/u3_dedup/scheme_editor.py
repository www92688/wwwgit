# 方案编辑器（U3）：按手法 param_schema 动态渲染参数编辑器
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ych.core.m3_dedup.techniques.base import ParamField
from ych.core.m3_dedup.techniques.registry import TechniqueRegistry


class SchemeEditor(QGroupBox):
    """给定 technique_params（[{id, params}]）渲染编辑表单 → collect() 读回。"""

    def __init__(self, registry: TechniqueRegistry,
                 parent: QWidget | None = None) -> None:
        super().__init__(self.tr("方案参数"), parent)
        self._registry = registry
        self._items: list[dict[str, object]] = []
        self._widgets: dict[str, dict[str, QWidget]] = {}
        # 表单放进常驻滚动容器：重度预设 5 手法 13+ 行远超右侧栏可用高度，
        # 布局被压缩时行会互相叠压（高分屏下尤其严重），空间不足必须滚动
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self._scroll)
        self.setLayout(box)
        self.set_items([])

    def retranslate(self) -> None:
        """语言切换：分组标题与手法名/参数标签重翻译（整表重建）。

        用 collect() 回读当前编辑值再重建——否则用户已调的参数会被重置。
        """
        self.setTitle(self.tr("方案参数"))
        self.set_items(self.collect())

    # ---- 渲染 ----
    def set_items(self, items: list[dict[str, object]]) -> None:
        self._items = [
            {"id": str(i["id"]),
             "params": dict(i["params"] or {})}   # type: ignore[call-overload]
            for i in items
        ]
        self._widgets.clear()

        form_box = QWidget(self)
        form = QFormLayout(form_box)
        form.setContentsMargins(12, 4, 12, 8)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        for tid, params in ((str(it["id"]), it["params"]) for it in self._items):
            technique = self._registry.get(tid)
            if technique is None:
                continue
            # 手法名/参数标签存于核心注册表（中文），经 tr() 查语言包翻译
            form.addRow(QLabel(f"▸ {self.tr(technique.display_name_zh)}"))
            editors: dict[str, QWidget] = {}
            for field_def in technique.param_schema:
                editor = self._editor_for(
                    field_def,
                    params.get(field_def.key) if isinstance(params, dict) else None,
                )
                form.addRow(self.tr(field_def.label_zh), editor)
                editors[field_def.key] = editor
            self._widgets[tid] = editors
        old = self._scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._scroll.setWidget(form_box)

    @staticmethod
    def _editor_for(field_def: ParamField, value: object) -> QWidget:
        v: object = field_def.default if value is None else value
        if field_def.type == "enum":
            combo = QComboBox()
            for choice in field_def.choices or []:
                combo.addItem(choice, choice)
            idx = combo.findText(str(v))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            return combo
        if field_def.type == "bool":
            check = QCheckBox()
            check.setChecked(bool(v))
            return check
        spin = QDoubleSpinBox()
        lo, hi = field_def.range if field_def.range else (0.0, 10.0)
        spin.setRange(float(lo), float(hi))
        spin.setDecimals(3)
        num = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float(lo)
        spin.setValue(num)
        return spin

    # ---- 读回 ----
    def collect(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for item in self._items:
            tid = str(item["id"])
            technique = self._registry.get(tid)
            raw: dict[str, object] = {}
            editors = self._widgets.get(tid, {})
            if technique is None:
                out.append({"id": tid, "params": item["params"]})
                continue
            for field_def in technique.param_schema:
                editor = editors.get(field_def.key)
                if editor is None:
                    continue
                if isinstance(editor, QComboBox):
                    data = editor.currentData()
                    raw[field_def.key] = (
                        data if isinstance(data, str) else editor.currentText()
                    )
                elif isinstance(editor, QCheckBox):
                    raw[field_def.key] = editor.isChecked()
                elif isinstance(editor, QDoubleSpinBox):
                    raw[field_def.key] = round(editor.value(), 3)
            out.append({"id": tid, "params": technique.validate_params(raw)})
        return out
