# 右上浮出 Toast 提示（非阻断反馈：DL010 上限、平台不可用、错误含「查看日志」）
# 多条 Toast 依宿主窗口右上角纵向堆叠，互不遮挡；超时后自动回收并重新排版。
from __future__ import annotations

import contextlib
import weakref
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

_GAP = 10          # 堆叠间距（px）
_MARGIN = 20       # 距宿主窗口右上角的边距
_MAX_STACK = 4     # 同屏最多条数，超出立即回收最旧一条

# 宿主窗口 → 存活 Toast 列表（弱键：窗口销毁时自动清空）
_ACTIVE: weakref.WeakKeyDictionary[QWidget, list[Toast]] = (
    weakref.WeakKeyDictionary()
)


class Toast(QWidget):
    """Toast.show(parent, message, error=False, log_dir=None)。"""

    def __init__(self, parent: QWidget | None = None,
                 message: str = "", error: bool = False,
                 log_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        # 半透明圆角卡片
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, False,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )

        label = QLabel(message)
        label.setObjectName("toastError" if error else "toastLabel")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setMaximumWidth(360)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(label)
        if error and log_dir is not None:
            btn = QPushButton("查看日志")
            btn.setObjectName("secondaryBtn")

            def _open_logs() -> None:
                QDesktopServices.openUrl(log_dir.as_uri())

            btn.clicked.connect(_open_logs)
            row.addWidget(btn)

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 10, 16, 10)
        box.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)
        self._host: QWidget | None = None

    @staticmethod
    def show_message(parent: QWidget | None, message: str,
                     error: bool = False, log_dir: Path | None = None,
                     timeout_ms: int = 4000) -> None:
        toast = Toast(parent, message, error, log_dir)
        toast.adjustSize()
        toast._present(timeout_ms)

    # ---- 展示与堆叠 ----
    def _present(self, timeout_ms: int) -> None:
        pw = self.parentWidget()
        host = pw.window() if pw is not None else None
        self._host = host
        stack: list[Toast] | None = None
        if host is not None:
            stack = _ACTIVE.get(host)
            if stack is None:
                stack = []
                _ACTIVE[host] = stack
            # 超出上限：最旧的立即回收
            while len(stack) >= _MAX_STACK:
                oldest = stack.pop(0)
                try:
                    oldest._timer.stop()
                    oldest.deleteLater()
                except RuntimeError:
                    pass
            stack.append(self)
        self._relayout()
        self.show()
        self.raise_()
        self._timer.start(timeout_ms)

    def _dismiss(self) -> None:
        host = self._host
        if host is not None:
            try:
                stack = _ACTIVE.get(host)
                if stack is not None:
                    with contextlib.suppress(ValueError):
                        stack.remove(self)
                    for t in list(stack):
                        with contextlib.suppress(RuntimeError):
                            t._relayout()
            except RuntimeError:
                pass
        self.deleteLater()

    def _relayout(self) -> None:
        """按堆叠顺序排布：宿主窗口右上角自上而下。"""
        host = self._host
        if host is None:
            self.move(_MARGIN, _MARGIN)
            return
        try:
            stack = _ACTIVE.get(host)
            origin = host.mapToGlobal(QPoint(0, 0))
            host_w = host.width()
        except RuntimeError:
            return
        if stack is None:
            return
        y = origin.y() + _MARGIN
        for t in stack:
            if t is self:
                x = origin.x() + host_w - self.width() - _MARGIN
                self.move(x, y)
                return
            y += t.height() + _GAP
