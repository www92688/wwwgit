# 下载队列（详设 12.3）：limit 截断 / 并发闸门 / 断点续传 / 归档落盘
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ych.common.cancellation import CancellationToken
from ych.common.schemas import VideoMeta
from ych.core.interfaces import IDownloadArchiveTarget
from ych.core.m1_capture.plugin_manager import PluginManager
from ych.core.m4_scheduler.task_scheduler import ManagedTask, TaskResult, TaskScheduler
from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s3_db.daos import DaosBundle
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m1")

# 进度回写节流间隔（详设 12.3：≥1s）
_PROGRESS_WRITE_INTERVAL_S = 1.0


class DownloadManager(QObject):
    """下载类任务执行体；经 M4 submit 派发，本类只实现 handle_one。"""

    # (download_task 行 id, state, progress 0~1, msg)
    item_updated = Signal(int, str, float, str)

    def __init__(
        self,
        scheduler: TaskScheduler,
        manager: PluginManager,
        archive: IDownloadArchiveTarget,
        workdirs: WorkDirManager,
        daos: DaosBundle,
        config: ConfigService,
    ) -> None:
        super().__init__()
        self._sched = scheduler
        self._manager = manager
        self._archive = archive
        self._workdirs = workdirs
        self._daos = daos
        self._config = config
        concurrency = max(1, int(config.get_typed("download_concurrency", int)))
        self.semaphore: threading.BoundedSemaphore = threading.BoundedSemaphore(concurrency)
        scheduler.register_handler("download", self.handle_one)

    # ---- 入队（详设 12.3 enqueue_downloads）----
    def enqueue_downloads(self, metas: list[VideoMeta], keyword: str, limit: int) -> int:
        """截取前 limit 条逐条提交（共享 batch_id 聚合进度）；超限入队 DL010 提示任务。

        返回实际入队的下载数。
        """
        from ych.common.schemas import TaskPayload

        selected = list(metas[:max(0, limit)])
        batch_id = uuid.uuid4().hex
        for meta in selected:
            payload = TaskPayload(type="download", data={
                "metas": [meta], "keyword": keyword, "limit": 1,
                "batch_id": batch_id,
            })
            self._sched.submit(payload)
        if len(metas) > len(selected):
            hint = TaskPayload(type="download", data={
                "keyword": keyword, "batch_id": batch_id,
                "dl010": True, "excluded": len(metas) - len(selected),
            })
            self._sched.submit(hint)
        return len(selected)

    def enqueue_resume(self, row_id: int, meta: VideoMeta, keyword: str) -> str:
        """崩溃恢复续传入口：复用既有 download_task 行（resume_row_ids）。"""
        from ych.common.schemas import TaskPayload

        payload = TaskPayload(type="download", data={
            "metas": [meta], "keyword": keyword, "limit": 1,
            "resume_row_ids": [row_id],
        })
        return self._sched.submit(payload)

    # ---- 执行体 ----
    def handle_one(self, task: ManagedTask) -> TaskResult:
        """M4 worker 调用：闸门 → 续传读库 → .part 下载 → 归档 → 状态回写。"""
        data = task.payload.data
        if data.get("dl010"):
            excluded = int(data.get("excluded") or 0)  # type: ignore[call-overload]
            msg = f"已达单次下载上限，其余 {excluded} 条未下载"
            self.item_updated.emit(-1, "limit_reached", 1.0, msg)
            return TaskResult(summary={"limit_reached": True, "excluded": excluded})

        metas_raw = data.get("metas") or []
        metas = [m for m in metas_raw if isinstance(m, VideoMeta)]  # type: ignore[attr-defined]
        if not metas:
            return TaskResult(summary={"skipped": "empty_metas"})
        meta = metas[0]
        keyword = str(data.get("keyword", ""))
        row_id = task.db_row_id

        self._acquire_slot(task.token)
        try:
            row = self._daos.downloads.get(row_id) if row_id is not None else None
            resume = self._daos.downloads.load_resume(row) if row is not None else None
            # 传给插件的是无后缀基名；S4 默认实现在其上加 .part（详设 12.3）
            temp_base = self._workdirs.workdir() / ".downloading" / uuid.uuid4().hex
            plugin = self._manager.get(meta.plugin_id)
            if plugin is None:
                raise RuntimeError(f"未注册的采集插件：{meta.plugin_id}")

            last_write = [0.0]

            def on_progress(ratio: float) -> None:
                now = time.monotonic()
                if now - last_write[0] < _PROGRESS_WRITE_INTERVAL_S:
                    return
                last_write[0] = now
                if row_id is not None:
                    self._daos.downloads.update_state(row_id, "running", progress=ratio)
                self.item_updated.emit(row_id or -1, "running", ratio, "")
                self._sched.emit_progress(task.task_id, ratio)

            logger.info("开始下载 %s/%s", meta.plugin_id, meta.video_key)
            state = plugin.download(meta, temp_base, on_progress, resume, task.token)
            # 续传时实际数据落在 resume.temp_path（S4 契约），以返回态为准
            final_temp = (
                Path(state.temp_path) if state and state.temp_path
                else Path(str(temp_base) + ".part")
            )
            dest = self._archive.archive_download(meta, final_temp, keyword, task.token)
            if row_id is not None:
                self._daos.downloads.set_dest(row_id, str(dest))
            self.item_updated.emit(row_id or -1, "success", 1.0, str(dest))
            return TaskResult(summary={"dest": str(dest)}, output_path=str(dest))
        finally:
            self.semaphore.release()

    def _acquire_slot(self, token: CancellationToken | None) -> None:
        """并发闸门（≥3 并行的真正约束），轮询中响应协作式取消。"""
        while not self.semaphore.acquire(blocking=False):
            if token is not None:
                token.check()
            time.sleep(0.02)
