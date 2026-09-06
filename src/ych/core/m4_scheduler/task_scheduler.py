# 任务调度中心对外唯一入口（详设十一章；T-2/T-3/T-4 线程约定）
from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

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
        gate_held = False
        retrying = False
        try:
            # 处理类并发闸门在置 running 之前获取：排队等待期不产生"伪 running"
            # 行（崩溃恢复不会把从未开始的任务误判为中断）
            if task.payload.type != "download":
                sched.acquire_process_slot(task.token)
                gate_held = True
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
                retrying = True
                sched._retry_requested.emit(task, delay)
                return
            sched._finish(task, "failed", message=str(exc),
                          error_code=code, err=exc)
        finally:
            if gate_held:
                sched.release_process_slot()
            # 重试路径任务尚未结束：不计入批次完成数（否则 done > total）
            if not retrying:
                sched._on_worker_done(task)


class TaskScheduler(QObject):
    """队列 / 并发控制 / 状态机 / 重试 / 失败记录 / 崩溃恢复的编排者。"""

    task_submitted = Signal(str)                 # task_id
    task_state = Signal(str, str, str)           # task_id, state, message
    task_progress = Signal(str, float)           # task_id, 0~1
    queue_stats = Signal(int, int)               # done, total（按 batch 聚合）
    download_row_registered = Signal(int, str, str)   # row_id, platform, title
    # task_id, type, state, message, summary（终态反馈；UI 按类型路由）
    task_done = Signal(str, str, str, str, object)
    _retry_requested = Signal(object, float)     # ManagedTask, 退避秒数（跨线程排队回主循环）

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
        # 处理类并发闸门（T-7）：读取设置页 process_concurrency，
        # 下载类由 DownloadManager.semaphore（download_concurrency）自行控制
        process_conc = max(1, int(config.get_typed("process_concurrency", int)))
        self._process_sem = threading.BoundedSemaphore(process_conc)
        self._retry = RetryController(config)
        self._fails = FailRecordManager(daos.fails)
        self._recovery = CrashRecovery(daos)
        self._retry_requested.connect(self._do_resubmit)
        self._batch_done: dict[str, int] = defaultdict(int)
        self._batch_total: dict[str, int] = defaultdict(int)
        self._batch_lock = threading.Lock()

    # ---- handler 注册 ----
    def register_handler(self, task_type: str,
                         handler: Callable[[ManagedTask], TaskResult]) -> None:
        """M1/M2/M3 启动时注册各自执行体。"""
        self._handlers[task_type] = handler

    # ---- 提交 ----
    def submit(self, payload: TaskPayload, priority: int = 0) -> str:
        task_id = uuid.uuid4().hex
        task = ManagedTask(task_id=task_id, payload=payload)
        self._evict_terminal_tasks()
        self._persist_new(task)
        self._tasks[task_id] = task
        batch_id = payload.data.get("batch_id")
        if batch_id is not None:
            self._batch_total[str(batch_id)] += 1
        self.task_submitted.emit(task_id)
        self._dispatch(task, priority)
        return task_id

    _TERMINAL_STATES = frozenset(
        {"success", "failed", "skipped", "interrupted", "canceled"},
    )

    def _evict_terminal_tasks(self) -> None:
        """内存任务表超阈值时回收终态条目（长会话防无限增长）。"""
        if len(self._tasks) < 512:
            return
        for tid in [
            tid for tid, t in self._tasks.items()
            if t.state in self._TERMINAL_STATES
        ]:
            del self._tasks[tid]

    def _dispatch(self, task: ManagedTask, priority: int = 0) -> None:
        if task.state != "pending":
            return
        self._pool.start(TaskWorker(self, task), priority)

    def _do_resubmit(self, task: object, delay: float = 0.0) -> None:
        assert isinstance(task, ManagedTask)
        # 指数退避在主线程经 QTimer 延迟重投（信号跨线程已排队到主循环）
        if delay > 0:
            QTimer.singleShot(int(delay * 1000), lambda: self._resubmit(task))
        else:
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

    def cancel_by_row(self, row_id: int) -> int:
        """按 download_task/process_task 行 id 取消进行中任务，返回取消数。"""
        n = 0
        for task in self._tasks.values():
            if task.db_row_id == row_id and task.state in ("pending", "running"):
                task.token.cancel()
                n += 1
        if n == 0:
            raise AppError(ERR_TASK_NOT_FOUND, f"行 {row_id} 没有进行中的任务")
        return n

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
                # 重新提交同一素材：优先复用带断点状态的历史行（断点续传）
                m0 = metas[0]
                resumable = self._daos.downloads.find_resumable(
                    m0.plugin_id, m0.video_key,
                )
                if resumable is not None:
                    task.db_row_id = resumable.id
                    # 队列视图可能刚从重启后重建：补发注册信号（幂等）
                    self.download_row_registered.emit(
                        task.db_row_id, m0.plugin_id, m0.title,
                    )
                else:
                    task.db_row_id = self._daos.downloads.create(m0, keyword)
                    self.download_row_registered.emit(
                        task.db_row_id, m0.plugin_id, m0.title,
                    )
        else:
            items_raw = data.get("items") or []
            items: list[dict[str, object]] = list(items_raw)  # type: ignore[call-overload]
            src = Path(str(items[0]["src"])) if items else Path(".")
            if not items:
                # compare 类载荷无 items，取首个 src 落库（避免脏 "." 行）
                srcs_raw = data.get("srcs") or []
                srcs = [str(s) for s in srcs_raw  # type: ignore[attr-defined]
                        if str(s)]
                if srcs:
                    src = Path(srcs[0])
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
        try:
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
        except Exception:
            # 终态落库失败不阻断状态翻转与信号：内存态照常收敛，
            # 遗留的 running 行由下次启动的崩溃恢复兜底
            logger.exception("任务 %s 终态落库失败", task.task_id)
        finally:
            task.state = state
            self.task_state.emit(task.task_id, state, message)
            self.task_done.emit(
                task.task_id, task.payload.type, state, message,
                dict(summary or {}),
            )

    def _on_worker_done(self, task: ManagedTask) -> None:
        batch_id = task.payload.data.get("batch_id")
        if batch_id is not None:
            key = str(batch_id)
            with self._batch_lock:      # 多 worker 并发收尾，计数需互斥
                self._batch_done[key] += 1
                done, total = self._batch_done[key], self._batch_total[key]
            self.queue_stats.emit(done, total)















