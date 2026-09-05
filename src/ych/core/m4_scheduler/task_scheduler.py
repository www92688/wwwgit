# 任务调度中心对外唯一入口（详设十一章；T-2/T-3/T-4 线程约定）
from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ych.common.cancellation import CancellationToken, SkippedSignal, TaskCanceled
from ych.common.errors import ERR_TASK_NOT_FOUND, AppError
from ych.common.schemas import TaskPayload, VideoMeta
from ych.core.m4_scheduler.crash_recovery import CrashRecovery, CrashRecoverySummary
from ych.core.m4_scheduler.fail_record_manager import FailRecordManager
from ych.core.m4_scheduler.retry_controller import RetryController
from ych.services.s3_db.daos import DaosBundle
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m4")


@dataclass
class ManagedTask:
    task_id: str
    payload: TaskPayload
    state: str = "pending"          # TaskState 七态
    retry_count: int = 0
    db_row_id: int | None = None    # download_task / process_task 行 id
    token: CancellationToken = field(default_factory=CancellationToken)
    error_code: str | None = None


@dataclass
class TaskResult:
    """handler 执行体统一返回值。"""

    summary: dict[str, object] = field(default_factory=dict)
    output_path: str | None = None




HandlerFn = Callable[[ManagedTask], TaskResult]



# 处理类任务并发闸门默认值（T-7；下载类由 DownloadManager.semaphore 自行控制）
_PROCESS_CONCURRENCY = 2


class TaskWorker(QRunnable):
    """五步执行体：先写库 running → handler → 成功/取消/跳过/失败分派。

    异常仅在 worker 内捕获，不逃逸 run()（批量部分失败隔离）。
    """

    def __init__(self, scheduler: TaskScheduler, task: ManagedTask) -> None:
        super().__init__()
        self._sched = scheduler
        self._task = task

    def run(self) -> None:
        task, sched = self._task, self._sched
        try:
            sched._mark_running(task)
            result = sched._handlers[task.payload.type](task)
            sched._finish(task, "success",
                          summary=result.summary,
                          output_path=result.output_path)
        except TaskCanceled as exc:
            sched._finish(task, "canceled", message=str(exc))
        except SkippedSignal as exc:
            sched._finish(task, "skipped", message=str(exc))
        except Exception as exc:
            code = exc.code if isinstance(exc, AppError) else "UNKNOWN"
            if isinstance(exc, AppError) and sched._retry.should_retry(task, exc):
                task.retry_count += 1
                if task.db_row_id is not None and task.payload.type != "download":
                    sched._daos.processes.increment_retry(task.db_row_id)
                delay = sched._retry.backoff_seconds(task.retry_count - 1)
                logger.info("task %s 将在 %ss 后第 %d 次重试",
                            task.task_id, delay, task.retry_count)
                sched._retry_requested.emit(task)
                return
            sched._finish(task, "failed", message=str(exc),
                          error_code=code, err=exc)
        finally:
            sched._on_worker_done(task)


class TaskScheduler(QObject):
    """队列 / 并发控制 / 状态机 / 重试 / 失败记录 / 崩溃恢复的编排者。"""

    task_submitted = Signal(str)                 # task_id
    task_state = Signal(str, str, str)           # task_id, state, message
    task_progress = Signal(str, float)           # task_id, 0~1
    queue_stats = Signal(int, int)               # done, total（按 batch 聚合）
    download_row_registered = Signal(int, str, str)   # row_id, platform, title
    _retry_requested = Signal(object)            # ManagedTask（跨线程排队回主循环）

    def __init__(
        self,
        pool: QThreadPool,
        config: ConfigService,
        daos: DaosBundle,
    ) -> None:
        super().__init__()
        self._pool = pool
        self._config = config
        self._daos = daos
        self._handlers: dict[str, HandlerFn] = {}
        self._tasks: dict[str, ManagedTask] = {}
        self._process_sem = threading.BoundedSemaphore(_PROCESS_CONCURRENCY)
        self._retry = RetryController(config)
        self._fails = FailRecordManager(daos.fails)
        self._recovery = CrashRecovery(daos)
        self._retry_requested.connect(self._do_resubmit)
        self._batch_done: dict[str, int] = defaultdict(int)
        self._batch_total: dict[str, int] = defaultdict(int)

    # ---- handler 注册 ----
    def register_handler(self, task_type: str,
                         handler: Callable[[ManagedTask], TaskResult]) -> None:
        """M1/M2/M3 启动时注册各自执行体。"""
        self._handlers[task_type] = handler

    # ---- 提交 ----
    def submit(self, payload: TaskPayload, priority: int = 0) -> str:
        task_id = uuid.uuid4().hex
        task = ManagedTask(task_id=task_id, payload=payload)
        self._persist_new(task)
        self._tasks[task_id] = task
        batch_id = payload.data.get("batch_id")
        if batch_id is not None:
            self._batch_total[str(batch_id)] += 1
        self.task_submitted.emit(task_id)
        self._dispatch(task, priority)
        return task_id

    def _dispatch(self, task: ManagedTask, priority: int = 0) -> None:
        if task.state != "pending":
            return
        self._pool.start(TaskWorker(self, task), priority)

    def _do_resubmit(self, task: object) -> None:
        assert isinstance(task, ManagedTask)
        self._resubmit(task)

    def _resubmit(self, task: ManagedTask) -> None:
        task.state = "pending"
        self._dispatch(task)

    # ---- 取消 ----
    def cancel(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            raise AppError(ERR_TASK_NOT_FOUND, f"任务不存在：{task_id}")
        task.token.cancel()

    # ---- 失败列表重建 ----
    def submit_from_fail_record(self, record_id: int) -> str:
        payload = self._fails.rebuild_payload(record_id)
        return self.submit(payload)

    # ---- 崩溃恢复 ----
    def recover_on_startup(self) -> CrashRecoverySummary:
        return self._recovery.scan(resume_cb=self._resume_download_task)

    def _resume_download_task(self, row_id: int, meta_json: str,
                              keyword: str) -> None:
        """崩溃恢复：带 resume_state 的下载任务重新入队续传。"""
        import json

        from ych.common.schemas import VideoMeta

        meta = VideoMeta(**json.loads(meta_json))
        payload = TaskPayload(type="download", data={
            "metas": [meta], "keyword": keyword, "limit": 1,
            "resume_row_ids": [row_id],
        })
        self.submit(payload)

    # ---- 内部：持久化 ----
    def _persist_new(self, task: ManagedTask) -> None:
        p = task.payload
        data: dict[str, object] = p.data
        if p.type == "download":
            metas_raw = data.get("metas") or []
            metas = [m for m in metas_raw if isinstance(m, VideoMeta)]  # type: ignore[attr-defined]
            keyword = str(data.get("keyword", ""))
            resume_ids_raw = data.get("resume_row_ids") or []
            resume_ids = [int(x) for x in resume_ids_raw]  # type: ignore[attr-defined]
            if resume_ids:
                task.db_row_id = resume_ids[0]
            elif metas:
                task.db_row_id = self._daos.downloads.create(metas[0], keyword)
                self.download_row_registered.emit(
                    task.db_row_id, metas[0].plugin_id, metas[0].title,
                )
        else:
            items_raw = data.get("items") or []
            items: list[dict[str, object]] = list(items_raw)  # type: ignore[call-overload]
            src = Path(str(items[0]["src"])) if items else Path(".")
            task.db_row_id = self._daos.processes.create(p.type, src, data)

    def _mark_running(self, task: ManagedTask) -> None:
        task.state = "running"
        if task.db_row_id is not None:
            if task.payload.type == "download":
                self._daos.downloads.update_state(task.db_row_id, "running")
            else:
                self._daos.processes.mark_running(task.db_row_id)
        self.task_state.emit(task.task_id, "running", "")

    # ---- 进度透传（handler 调用）----
    def emit_progress(self, task_id: str, ratio: float) -> None:
        self.task_progress.emit(task_id, max(0.0, min(1.0, ratio)))

    # ---- 处理类并发闸门（T-7）----
    def acquire_process_slot(self, token: CancellationToken | None = None) -> None:
        while not self._process_sem.acquire(blocking=False):
            if token is not None:
                token.check()
            import time

            time.sleep(0.02)

    def release_process_slot(self) -> None:
        self._process_sem.release()

    # ---- 终态落库 + 信号 ----
    def _finish(
        self,
        task: ManagedTask,
        state: str,
        summary: dict[str, object] | None = None,
        output_path: str | None = None,
        message: str = "",
        error_code: str | None = None,
        err: Exception | None = None,
    ) -> None:
        # 终态可见性顺序（竞态加固）：先提交全部持久化副作用（任务行 + 失败记录），
        # 再翻转内存态、发信号。若先翻 state，外部观察者（测试轮询/UI 读 DAO）
        # 可能在写库完成前读到终态内存态——曾先后造成"行仍 running"
        # 与"失败记录尚未落库"两类断言竞态
        rid = task.db_row_id
        if state == "failed" and err is not None:
            self._fails.record(task, err)
        if rid is not None:
            if task.payload.type == "download":
                if output_path:
                    self._daos.downloads.set_dest(rid, output_path)
                progress = 1.0 if state == "success" else None
                self._daos.downloads.update_state(
                    rid, state,  # type: ignore[arg-type]
                    progress=progress, error_code=error_code,
                )
            else:
                if state == "success" and output_path:
                    self._daos.processes.set_dst(rid, Path(output_path))
                self._daos.processes.finish(
                    rid, state, summary, error_code,  # type: ignore[arg-type]
                )
        task.state = state
        self.task_state.emit(task.task_id, state, message)

    def _on_worker_done(self, task: ManagedTask) -> None:
        batch_id = task.payload.data.get("batch_id")
        if batch_id is not None:
            key = str(batch_id)
            self._batch_done[key] += 1
            self.queue_stats.emit(self._batch_done[key], self._batch_total[key])















