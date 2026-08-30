# 崩溃恢复（详设 11.3）：启动扫描遗留 running 任务 → interrupted → 分支处理
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ych.services.s3_db.daos import DaosBundle, DownloadTaskRow, ProcessTaskRow

logger = logging.getLogger("ych.m4")


@dataclass
class CrashRecoverySummary:
    resumed_downloads: int = 0     # 已重新入队续传的下载任务数
    moved_to_fail: int = 0         # 转入失败列表的中断任务数


class _ResumeCallback(Protocol):
    def __call__(self, row_id: int, meta_json: str, keyword: str) -> None: ...


class CrashRecovery:
    """扫描 download_task / process_task 中遗留的 running 状态行。

    - 下载类：置 interrupted；有 resume_state → 回调重建 pending 续传；
      无 resume_state → 写失败记录；
    - 处理类：置 interrupted → 写失败记录「软件中断，请重新处理」。
    """

    def __init__(self, daos: DaosBundle) -> None:
        self._daos = daos

    def scan(self, resume_cb: _ResumeCallback | None = None) -> CrashRecoverySummary:
        summary = CrashRecoverySummary()

        # ---- 下载任务 ----
        for drow in self._daos.downloads.list_by_status("running"):
            assert isinstance(drow, DownloadTaskRow)
            self._daos.downloads.update_state(drow.id, "interrupted")
            if drow.resume_state:
                if resume_cb is not None:
                    resume_cb(drow.id, drow.video_meta, drow.keyword)
                    summary.resumed_downloads += 1
                    logger.info("download %s requeued for resume", drow.id)
            else:
                self._daos.fails.add(
                    file_name=drow.keyword,
                    reason="软件中断，下载未完成",
                    code="DL001",
                    task_type="download",
                    payload={"video_meta": drow.video_meta,
                             "keyword": drow.keyword},
                )
                summary.moved_to_fail += 1

        # ---- 处理任务 ----
        for prow in self._daos.processes.list_interrupted():
            assert isinstance(prow, ProcessTaskRow)
            self._daos.processes.finish(prow.id, "interrupted", None, None)
            src_name = Path(prow.src_path).name if prow.src_path else "?"
            self._daos.fails.add(
                file_name=src_name,
                reason="软件中断，请重新处理",
                code="TASK004",
                task_type=prow.task_type,
                payload={"type": prow.task_type,
                         "data": {"items": [{"src": prow.src_path}]}},
            )
            summary.moved_to_fail += 1
            logger.info("process %s moved to fail list", prow.id)

        return summary


