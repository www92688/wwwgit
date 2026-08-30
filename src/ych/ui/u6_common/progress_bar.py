# 进度条组件（带状态文字，供队列/批量视图复用）
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget


class LabeledProgressBar(QWidget):
    """进度条 + 右侧百分比/状态文本。"""

    def __init__(self, label_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._label = QLabel(label_text)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._bar, 1)
        row.addWidget(self._label)

    def set_progress(self, ratio: float) -> None:
        self._bar.setValue(int(max(0.0, min(ratio, 1.0)) * 100))
        self._label.setText(f"{int(min(ratio, 1.0) * 100)}%")

    def set_status(self, text: str) -> None:
        self._label.setText(text)

    def reset(self) -> None:
        self._bar.setValue(0)
        self._label.setText("")
