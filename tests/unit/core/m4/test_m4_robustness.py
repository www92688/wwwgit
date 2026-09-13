# 重试机制健壮性：退避在工作线程内等待（不依赖主线程事件循环），
# 退避期取消直达 canceled 终态（旧 QTimer 方案需等 30s 重投后才发现取消）
from __future__ import annotations

import time

import pytest

from ych.common.errors import ERR_NET_TIMEOUT, AppError
from ych.common.schemas import TaskPayload
from ych.core.m4_scheduler.task_scheduler import ManagedTask, TaskResult
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database
from ych.services.s5_base.config_service import ConfigService


@pytest.fixture
def env(qtbot, tmp_path):
    cfg = ConfigService()
    cfg.set("retry_backoff_seconds", [0.0, 0.0])
    daos = make_daos(Database(tmp_path / "app.db"))
    from PySide6.QtCore import QThreadPool

    from ych.core.m4_scheduler.task_scheduler import TaskScheduler

    pool = QThreadPool()
    pool.setMaxThreadCount(4)
    sched = TaskScheduler(pool, cfg, daos)
    yield {"sched": sched, "daos": daos, "cfg": cfg, "qtbot": qtbot}
    pool.clear()
    pool.waitForDone(2000)


def test_cancel_during_backoff_reaches_canceled_quickly(env) -> None:
    """退避 30s 内取消：应 <5s 进 canceled，而不是等退避结束。"""
    import pytest

    calls = {"n": 0}

    def handler(_task: ManagedTask) -> TaskResult:
        calls["n"] += 1
        raise AppError(ERR_NET_TIMEOUT, "网络超时")

    env["sched"].register_handler("dedup", handler)
    env["cfg"].set("retry_backoff_seconds", [30.0])
    tid = env["sched"].submit(TaskPayload(
        type="dedup", data={"items": [{"src": "b.mp4"}]}))

    # 等首次执行进入失败→退避（任务保持 running）
    deadline = time.monotonic() + 8
    while env["sched"]._tasks[tid].state != "running":
        if time.monotonic() > deadline:
            pytest.fail("任务未进入 running")
        env["qtbot"].wait(20)
    while calls["n"] < 1 and time.monotonic() < deadline:
        env["qtbot"].wait(20)

    env["sched"].cancel(tid)   # CancellationToken：退避循环秒退
    deadline = time.monotonic() + 5
    state = env["sched"]._tasks[tid].state
    while state not in ("canceled",) and time.monotonic() < deadline:
        env["qtbot"].wait(20)
        state = env["sched"]._tasks[tid].state
    assert state == "canceled"
    assert calls["n"] == 1     # 取消发生在退避期：handler 未被再次调用


def test_retry_still_fires_without_main_loop_roundtrip(env) -> None:
    """零退避重试照常工作（回归：重试不再经主线程 QTimer 信号）。"""
    class _Handler:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _task: ManagedTask) -> TaskResult:
            self.calls += 1
            if self.calls == 1:
                raise AppError(ERR_NET_TIMEOUT, "瞬态失败")
            return TaskResult(summary={}, output_path=None)

    h = _Handler()
    env["sched"].register_handler("dedup", h)
    env["cfg"].set("retry_backoff_seconds", [0.0])
    tid = env["sched"].submit(TaskPayload(
        type="dedup", data={"items": [{"src": "b.mp4"}]}))
    deadline = time.monotonic() + 8
    while h.calls < 2 and time.monotonic() < deadline:
        env["qtbot"].wait(20)
    deadline = time.monotonic() + 8
    state = env["sched"]._tasks[tid].state
    while state != "success" and time.monotonic() < deadline:
        env["qtbot"].wait(20)
        state = env["sched"]._tasks[tid].state
    assert state == "success" and h.calls == 2
