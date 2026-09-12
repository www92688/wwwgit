# 底部状态栏任务摘要（TaskStatusLabel）聚合逻辑
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal


class _FakeScheduler(QObject):
    task_submitted = Signal(str)
    task_state = Signal(str, str, str)
    task_progress = Signal(str, float)
    queue_stats = Signal(int, int)


@pytest.fixture
def status(qtbot):
    from PySide6.QtWidgets import QWidget

    from ych.ui.u0_main.main_window import TaskStatusLabel

    host = QWidget()
    qtbot.addWidget(host)
    # host 必须随 fixture 存活：qtbot 不持强引用，父对象被回收会连带
    # 销毁子控件（C++ 对象已删 → 后续访问抛 RuntimeError）
    label = TaskStatusLabel(host)
    sched = _FakeScheduler()
    label.attach(sched)
    return label, sched, host


def test_idle_then_running(status) -> None:
    label, sched, _host = status
    assert "就绪" in label.text()

    sched.task_submitted.emit("t1")
    assert "进行中 1 项" in label.text()
    assert "平均进度 0%" in label.text()

    sched.task_progress.emit("t1", 0.45)
    assert "平均进度 45%" in label.text()

    sched.task_submitted.emit("t2")
    sched.task_progress.emit("t2", 0.75)
    assert "进行中 2 项" in label.text()
    assert "平均进度 60%" in label.text()   # (0.45 + 0.75) / 2


def test_terminal_back_to_idle(status) -> None:
    label, sched, _host = status
    sched.task_submitted.emit("t1")
    sched.task_progress.emit("t1", 0.9)
    sched.task_state.emit("t1", "success", "")
    assert "就绪" in label.text()
    assert "进行中" not in label.text()


def test_batch_progress_and_reset(status) -> None:
    label, sched, _host = status
    sched.task_submitted.emit("t1")
    sched.queue_stats.emit(2, 5)
    assert "批次 2/5" in label.text()
    sched.queue_stats.emit(5, 5)
    assert "批次" not in label.text()       # 批次完成即清空显示


def test_progress_clamped(status) -> None:
    label, sched, _host = status
    sched.task_submitted.emit("t1")
    sched.task_progress.emit("t1", 1.7)
    assert "平均进度 100%" in label.text()
