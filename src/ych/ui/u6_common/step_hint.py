# 工作台顶部步骤引导条（① → ② → ③，呼应「三步工作台」设计）
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

_CIRCLED = "①②③④⑤⑥⑦⑧⑨"


class StepHint(QLabel):
    def __init__(self, steps: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stepHint")
        self.setTextFormat(Qt.TextFormat.RichText)
        self.set_steps(steps)

    def set_steps(self, steps: list[str]) -> None:
        """渲染/重渲染步骤条（语言切换重翻译时传入新文案再调一次）。"""
        parts: list[str] = []
        for i, text in enumerate(steps):
            num = _CIRCLED[i] if i < len(_CIRCLED) else f"{i + 1}."
            parts.append(
                f'<span style="color:#4c6ef5; font-weight:600;">{num}</span> {text}',
            )
        arrow = '<span style="color:#c2c9d6;">&nbsp;→&nbsp;</span>'
        self.setText(arrow.join(parts))
