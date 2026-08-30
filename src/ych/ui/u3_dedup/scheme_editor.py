# 方案编辑器（U3）：按手法 param_schema 动态渲染参数编辑器
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ych.core.m3_dedup.techniques.base import ParamField
from ych.core.m3_dedup.techniques.registry import TechniqueRegistry


class SchemeEditor(QGroupBox):
    """给定 technique_params（[{id, params}]）渲染编辑表单 → collect() 读回。"""

    def __init__(self, registry: TechniqueRegistry,
                 parent: QWidget | None = None) -> None:
        super().__init__("方案参数", parent)
        self._registry = registry
        self._items: list[dict[str, object]] = []
        self._widgets: dict[str, dict[str, QWidget]] = {}
        self.setLayout(QVBoxLayout(self))
        self.set_items([])

    # ---- 渲染 ----
    def set_items(self, items: list[dict[str, object]]) -> None:
        self._items = [
            {"id": str(i["id"]),
             "params": dict(i["params"] or {})}   # type: ignore[call-overload]
            for i in items
        ]
        layout = self.layout()
        assert layout is not None
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._widgets.clear()

        form_box = QWidget(self)
        form = QFormLayout(form_box)
        form.setContentsMargins(0, 0, 0, 0)
        for tid, params in ((str(it["id"]), it["params"]) for it in self._items):
            technique = self._registry.get(tid)
            if technique is None:
                continue
            form.addRow(QLabel(f"▸ {technique.display_name_zh}"))
            editors: dict[str, QWidget] = {}
            for field_def in technique.param_schema:
                editor = self._editor_for(
                    field_def,
                    params.get(field_def.key) if isinstance(params, dict) else None,
                )
                form.addRow(field_def.label_zh, editor)
                editors[field_def.key] = editor
            self._widgets[tid] = editors
        layout.addWidget(form_box)

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
