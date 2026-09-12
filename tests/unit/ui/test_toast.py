# Toast 堆叠行为测试：多条不重叠、超时回收后重新排版
from __future__ import annotations

import pytest


@pytest.fixture
def host(qtbot):
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    w.resize(800, 600)
    qtbot.addWidget(w)
    return w


def _toasts_of(host):   # type: ignore[no-untyped-def]
    from ych.ui.u6_common.toast import _ACTIVE

    return _ACTIVE.get(host, [])


def test_toast_stack_no_overlap(qtbot, host) -> None:
    from PySide6.QtCore import QPoint

    from ych.ui.u6_common.toast import Toast

    Toast.show_message(host, "第一条")
    Toast.show_message(host, "第二条")
    Toast.show_message(host, "第三条", error=True)
    stack = _toasts_of(host)
    assert len(stack) == 3
    # 纵向依次排列：后一条的 y = 前一条 y + 高度 + 间距
    from itertools import pairwise

    for prev, cur in pairwise(stack):
        assert cur.y() == prev.y() + prev.height() + 10
    # 右对齐宿主窗口（客户端区右上角，mapToGlobal 不含窗框偏移）
    origin = host.mapToGlobal(QPoint(0, 0))
    for t in stack:
        assert t.x() + t.width() == origin.x() + host.width() - 20


def test_toast_dismiss_relayout(qtbot, host) -> None:
    from PySide6.QtCore import QPoint

    from ych.ui.u6_common.toast import Toast

    Toast.show_message(host, "第一条")
    Toast.show_message(host, "第二条")
    stack = _toasts_of(host)
    second = stack[1]
    second._dismiss()
    assert len(stack) == 1
    # 回到顶部位置（客户端区原点 + 边距）
    assert stack[0].y() == host.mapToGlobal(QPoint(0, 0)).y() + 20


def test_toast_max_stack(qtbot, host) -> None:
    from ych.ui.u6_common.toast import Toast

    for i in range(6):
        Toast.show_message(host, f"第{i}条")
    stack = _toasts_of(host)
    assert len(stack) <= 4
