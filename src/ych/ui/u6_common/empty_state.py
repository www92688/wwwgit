# 空状态占位组件（列表无数据时）
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class EmptyState(QWidget):
    def __init__(self, text: str = "暂无数据", hint: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel(text)
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_label = QLabel(hint)
        hint_label.setObjectName("emptyHint")
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(hint_label)
        layout.addStretch(1)


class _ResizeFilter(QObject):
    """视口尺寸变化时让空状态铺满视口。"""

    def __init__(self, overlay: EmptyState) -> None:
        super().__init__(overlay)
        self._overlay = overlay

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            pw = self._overlay.parentWidget()
            if pw is not None:
                self._overlay.setGeometry(pw.rect())
        return False


def attach_empty_state(
    view: QWidget, title: str, hint: str = "",
    is_empty: Callable[[], bool] | None = None,
) -> EmptyState:
    """在 item view（或普通容器）上叠加空状态；返回 overlay 供显隐控制。

    - view 是 QAbstractItemView：叠加到 viewport，随视口缩放；
    - is_empty 缺省用「子项数为 0」判断（QListWidget/QTreeWidget 适用），
      无法判断的容器恒为 False（由调用方驱动显隐）。
    """
    target = view.viewport() if isinstance(view, QAbstractItemView) else view
    overlay = EmptyState(title, hint, target)
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    overlay.setGeometry(target.rect())
    overlay.hide()
    filt = _ResizeFilter(overlay)
    target.installEventFilter(filt)

    if is_empty is None:
        count_fn = getattr(view, "count", None)
        tl_fn = getattr(view, "topLevelItemCount", None)
        if count_fn is not None or tl_fn is not None:
            def is_empty() -> bool:
                fn = count_fn if count_fn is not None else tl_fn
                assert fn is not None
                return int(fn()) == 0
        else:
            is_empty = lambda: False   # noqa: E731

    def refresh() -> None:
        overlay.setVisible(is_empty())
        if overlay.isVisible():
            overlay.setGeometry(target.rect())
            overlay.raise_()

    view._empty_state = overlay          # type: ignore[attr-defined]
    view._refresh_empty_state = refresh  # type: ignore[attr-defined]
    refresh()
    return overlay
