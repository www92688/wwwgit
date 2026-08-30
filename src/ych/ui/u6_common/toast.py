# 右上浮出 Toast 提示（非阻断反馈：DL010 上限、平台不可用、错误含「查看日志」）
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class Toast(QWidget):
    """Toast.show(parent, message, error=False, log_dir=None)。"""

    def __init__(self, parent: QWidget | None = None,
                 message: str = "", error: bool = False,
                 log_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        )

        label = QLabel(message)
        label.setObjectName("toastError" if error else "toastLabel")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        row = QHBoxLayout()
        row.addWidget(label)
        if error and log_dir is not None:
            btn = QPushButton("查看日志")
            btn.setObjectName("secondaryBtn")

            def _open_logs() -> None:
                QDesktopServices.openUrl(log_dir.as_uri())

            btn.clicked.connect(_open_logs)
            row.addWidget(btn)

        box = QVBoxLayout(self)
        box.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.deleteLater)

    @staticmethod
    def show_message(parent: QWidget | None, message: str,
                     error: bool = False, log_dir: Path | None = None,
                     timeout_ms: int = 4000) -> None:
        toast = Toast(parent, message, error, log_dir)
        toast.adjustSize()
        if parent is not None:
            pos = parent.mapToGlobal(
                QPoint(max(parent.width() - toast.width() - 24, 8), 24)
            )
            toast.move(pos)
        else:
            toast.move(64, 64)
        toast.show()
        toast.raise_()
        toast._timer.start(timeout_ms)
