# M4 调度中心测试（对照 11.5 / tasks/08-m4-scheduler.md）
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from ych.common.cancellation import TaskCanceled
from ych.common.errors import ERR_MED_FORMAT_UNSUPPORTED, ERR_NET_TIMEOUT, AppError
from ych.common.schemas import TaskPayload, VideoMeta
from ych.core.m4_scheduler.task_scheduler import (
    ManagedTask,
    TaskResult,
    TaskScheduler,
)
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database
from ych.services.s5_base.config_service import ConfigService


@dataclass
class SyncFakeHandler:
    """可编程成败/挂起的任务处理器（详设 17.2）。"""

    outcomes: list = field(default_factory=list)   # "ok" | AppError | TaskCanceled | SkippedSignal
    calls: int = 0
    hook: Callable[[ManagedTask], None] | None = None

    def __call__(self, task: ManagedTask) -> TaskResult:
        idx = min(self.calls, len(self.outcomes) - 1) if self.outcomes else -1
        self.calls += 1
        if self.hook is not None:
            self.hook(task)
        outcome = self.outcomes[idx] if self.outcomes and idx >= 0 else "ok"
        if isinstance(outcome, str) and outcome == "ok":
            return TaskResult(summary={"fake": True}, output_path="out/fake.mp4")
        raise outcome


@pytest.fixture
def env(qtbot, tmp_path):
    cfg = ConfigService()
    cfg.set("retry_backoff_seconds", [0.0, 0.0])   # 测试零退避
    db = Database(tmp_path / "app.db")
    daos = make_daos(db)

    from PySide6.QtCore import QThreadPool

    pool = QThreadPool()
    pool.setMaxThreadCount(4)
    sched = TaskScheduler(pool, cfg, daos)
    yield {"sched": sched, "daos": daos, "cfg": cfg, "qtbot": qtbot}
    pool.clear()
    pool.waitForDone(2000)


def _wait_states(env, task_id: str, terminal: set[str], timeout_s: float = 8.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        task = env["sched"]._tasks[task_id]
        if task.state in terminal:
            return task.state
        env["qtbot"].wait(20)
    return env["sched"]._tasks[task_id].state


def test_success_state_and_signals(env) -> None:
    handler = SyncFakeHandler()
    env["sched"].register_handler("preprocess", handler)
    payload = TaskPayload(type="preprocess",
                          data={"items": [{"src": "a.mp4", "ops": {}}]})
    tid = env["sched"].submit(payload)
    state = _wait_states(env, tid, {"success"})
    assert state == "success"
    row = env["daos"].processes.get(env["sched"]._tasks[tid].db_row_id)
    assert row is not None and row.status == "success"
    assert json.loads(row.result_summary or "{}") == {"fake": True}


def test_retry_then_success(env) -> None:
    # 前 1 次失败（NET001 可重试），第 2 次成功
    handler = SyncFakeHandler(outcomes=[AppError(ERR_NET_TIMEOUT, "超时"), "ok"])
    env["sched"].register_handler("dedup", handler)
    tid = env["sched"].submit(TaskPayload(
        type="dedup", data={"items": [{"src": "b.mp4"}]}))
    state = _wait_states(env, tid, {"success"}, timeout_s=10)
    assert state == "success"
    assert handler.calls == 2
    row = env["daos"].processes.get(env["sched"]._tasks[tid].db_row_id)
    assert row is not None and row.retry_count == 1


def test_retry_exhausted_writes_fail_record(env) -> None:
    err = AppError(ERR_MED_FORMAT_UNSUPPORTED, "格式不支持")   # MED003 不可重试
    handler = SyncFakeHandler(outcomes=[err])
    env["sched"].register_handler("compare", handler)
    tid = env["sched"].submit(TaskPayload(type="compare", data={"srcs": ["c.mp4"]}))
    _wait_states(env, tid, {"failed"})
    # 竞态加固：终态可见后失败记录可能仍在落库途中（覆盖率追踪会放大窗口），
    # 轮询等待而非立即断言
    fails: list = []
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        fails = env["daos"].fails.list_recent()
        if fails:
            break
        env["qtbot"].wait(20)
    assert len(fails) == 1
    assert fails[0].error_code == "MED003"
    assert fails[0].file_name == "c.mp4"


def test_cancel_running_task_task004_terminal(env) -> None:
    started = threading_event()

    def hook(task: ManagedTask) -> None:
        started.set()
        for _ in range(120):            # 挂起中轮询协作式取消令牌（T-4）
            if task.token.cancelled:
                raise TaskCanceled("用户取消")
            time.sleep(0.02)

    handler = SyncFakeHandler(hook=hook)
    env["sched"].register_handler("preprocess", handler)
    tid = env["sched"].submit(TaskPayload(
        type="preprocess", data={"items": [{"src": "d.mp4"}]}))
    started.wait(timeout=5)
    env["sched"].cancel(tid)
    state = _wait_states(env, tid, {"canceled"}, timeout_s=10)
    assert state == "canceled"


def threading_event():
    import threading

    return threading.Event()


def test_batch_partial_failure_isolation(env) -> None:
    """批量 10 条中 3 条抛错 → 其余 7 条全部 success。"""
    fail_codes = iter([ERR_MED_FORMAT_UNSUPPORTED] * 3)

    def handler(task: ManagedTask) -> TaskResult:
        code = next(fail_codes, None)
        if code:
            raise AppError(code, "不支持")
        return TaskResult(summary={"ok": 1})

    env["sched"].register_handler("dedup", handler)
    batch_id = "batch-x"
    ids = []
    for i in range(10):
        ids.append(env["sched"].submit(TaskPayload(
            type="dedup",
            data={"items": [{"src": f"v{i}.mp4"}],
                  "batch_id": batch_id})))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        states = [env["sched"]._tasks[t].state for t in ids]
        if all(s in {"success", "failed"} for s in states):
            break
        env["qtbot"].wait(30)
    states = [env["sched"]._tasks[t].state for t in ids]
    assert states.count("success") == 7
    assert states.count("failed") == 3


def test_crash_recovery_resume_and_fail_branches(env, tmp_path) -> None:
    daos = env["daos"]
    # 注册 download 假 handler，承接续传重建的任务
    env["sched"].register_handler("download", SyncFakeHandler())
    meta = VideoMeta(plugin_id="pexels", video_key="9")
    rid_resume = daos.downloads.create(meta, "地毯清洗")
    from ych.common.schemas import ResumeState

    daos.downloads.update_state(rid_resume, "running",
                                resume=ResumeState(downloaded_bytes=100))
    rid_nores = daos.downloads.create(meta, "压面条")
    daos.downloads.update_state(rid_nores, "running")
    pid = daos.processes.create("preprocess", tmp_path / "x.mp4", {})
    daos.processes.mark_running(pid)

    summary = env["sched"].recover_on_startup()
    assert summary.resumed_downloads == 1
    assert summary.moved_to_fail == 2
    # 无续传状态的下载行保持 interrupted；有续传的已被重新入队（非 interrupted）。
    # 续传行状态由异步 worker 翻转，轮询等待其离开 interrupted（消除调度竞态）。
    deadline = time.monotonic() + 8
    rows = {r.id for r in daos.downloads.list_by_status("interrupted")}
    while rid_resume in rows and time.monotonic() < deadline:
        env["qtbot"].wait(20)
        rows = {r.id for r in daos.downloads.list_by_status("interrupted")}
    assert rows == {rid_nores}
    fails = daos.fails.list_recent()
    reasons = [f.fail_reason for f in fails]
    assert "软件中断，请重新处理" in reasons
    assert any(f.fail_reason == "软件中断，下载未完成" and f.file_name == "压面条"
               for f in fails)


def test_submit_from_fail_record_rebuilds_payload(env) -> None:
    daos = env["daos"]
    rid = daos.fails.add(
        file_name="v.mp4", reason="转码失败", code="MED010",
        task_type="preprocess",
        payload={"type": "preprocess",
                 "data": {"items": [{"src": "v.mp4", "ops": {}}]}},
    )
    tid = env["sched"].submit_from_fail_record(rid)
    payload = env["sched"]._tasks[tid].payload
    assert payload.type == "preprocess"
    assert payload.data["items"][0]["src"] == "v.mp4"


