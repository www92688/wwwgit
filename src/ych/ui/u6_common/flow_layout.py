# 流式布局：子项按宽度逐行排布（预设供应商标签墙用），Qt 官方示例的类型化实现
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QLayout,
    QSizePolicy,
    QWidget,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QLayoutItem


class FlowLayout(QLayout):
    """内容换行的流式布局；间距取 style 中 layoutSpacing。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x, y = 0, 0
        line_height = 0
        right = rect.right()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_space()
            if next_x - self._h_space() > right and line_height > 0:
                x = 0
                y += line_height + self._v_space()
                line_height = 0
                next_x = x + hint.width() + self._h_space()
            if not test_only:
                item.setGeometry(QRect(
                    QPoint(x + rect.left(), y + rect.top()), hint,
                ))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.top() + self._v_space() + self._bottom_margin()

    def _h_space(self) -> int:
        # 部分系统样式 layoutSpacing 返回 -1/0，钳制最小间距防止标签贴死
        return max(self._smart_spacing(QSizePolicy.ControlType.PushButton), 8)

    def _v_space(self) -> int:
        return max(self._smart_spacing(QSizePolicy.ControlType.PushButton), 8)

    def _smart_spacing(self, control: QSizePolicy.ControlType) -> int:
        parent = self.parent()
        if parent is None:
            return -1
        if isinstance(parent, QWidget):
            return int(parent.style().layoutSpacing(
                control, control, Qt.Orientation.Horizontal,
            ))
        if isinstance(parent, QLayout):
            return int(parent.spacing())
        return -1

    def _bottom_margin(self) -> int:
        return 0
