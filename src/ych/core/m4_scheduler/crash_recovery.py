# 崩溃恢复（详设 11.3）：启动扫描遗留 running 任务 → interrupted → 分支处理
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ych.common.fsutil import SafeFileOps
from ych.services.s3_db.daos import DaosBundle, DownloadTaskRow, ProcessTaskRow

logger = logging.getLogger("ych.m4")

# 孤儿 .part 判定门槛：mtime 距今超过该值才删（防并发实例等极端竞态）
_ORPHAN_MIN_AGE_S = 3600.0


@dataclass
class CrashRecoverySummary:
    resumed_downloads: int = 0     # 已重新入队续传的下载任务数
    moved_to_fail: int = 0         # 转入失败列表的中断任务数
    removed_orphan_parts: int = 0  # .downloading/ 清理的孤儿临时文件数


class _ResumeCallback(Protocol):
    def __call__(self, row_id: int, meta_json: str, keyword: str) -> None: ...


class _WorkdirProvider(Protocol):
    def __call__(self) -> Path | None: ...


class CrashRecovery:
    """扫描 download_task / process_task 中遗留的 running 状态行。

    - 下载类：置 interrupted；有 resume_state → 回调重建 pending 续传；
      无 resume_state → 写失败记录；
    - 处理类：置 interrupted → 写失败记录「软件中断，请重新处理」；
    - 顺带清理 .downloading/ 下无任何任务引用的孤儿 .part（下载
      失败/取消后残留，会随时间累积）。
    """

    def __init__(
        self,
        daos: DaosBundle,
        workdir_provider: _WorkdirProvider | None = None,
    ) -> None:
        self._daos = daos
        self._workdir_provider = workdir_provider

    def scan(self, resume_cb: _ResumeCallback | None = None) -> CrashRecoverySummary:
        summary = CrashRecoverySummary()

        # ---- 下载任务 ----
        for drow in self._daos.downloads.list_by_status("running"):
            assert isinstance(drow, DownloadTaskRow)
            # 单行脏数据（如 meta JSON 损坏）只跳过该行，
            # 不中断整个恢复（否则剩余行永久滞留 running）
            try:
                self._recover_download_row(drow, resume_cb, summary)
            except Exception:
                logger.exception("download %s 恢复失败（跳过）", drow.id)

        # ---- 处理任务 ----
        for prow in self._daos.processes.list_interrupted():
            assert isinstance(prow, ProcessTaskRow)
            try:
                self._daos.processes.finish(prow.id, "interrupted", None, None)
                src_name = Path(prow.src_path).name if prow.src_path else "?"
                self._daos.fails.add(
                    file_name=src_name,
                    reason="软件中断，请重新处理",
                    code="TASK004",
                    task_type=prow.task_type,
                    payload=self._rebuild_payload(prow),
                )
                summary.moved_to_fail += 1
                logger.info("process %s moved to fail list", prow.id)
            except Exception:
                logger.exception("process %s 恢复失败（跳过）", prow.id)

        # ---- .downloading/ 孤儿清理（失败不影响恢复结果）----
        try:
            summary.removed_orphan_parts = self._cleanup_orphan_parts()
        except OSError as exc:
            logger.warning(".downloading 孤儿清理失败（忽略）：%s", exc)

        return summary

    def _recover_download_row(
        self, drow: DownloadTaskRow,
        resume_cb: _ResumeCallback | None, summary: CrashRecoverySummary,
    ) -> None:
        self._daos.downloads.update_state(drow.id, "interrupted")
        started = bool(drow.resume_state) or (drow.progress or 0) > 0
        if not started:
            # 并发闸门排队中崩溃：从未开始，直接重排队（无需续传状态）
            if resume_cb is not None:
                resume_cb(drow.id, drow.video_meta, drow.keyword)
                summary.resumed_downloads += 1
                logger.info("download %s (never started) requeued", drow.id)
        elif drow.resume_state:
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

    def _cleanup_orphan_parts(self) -> int:
        """删除 .downloading/ 下无任务引用且超过门槛时长的 .part 文件。

        保留集 = 所有非终态下载行 resume_state.temp_path（含失败/取消，
        供 find_resumable 续传复用）；其余文件属崩溃残留，没有任何任务
        会再从它们续传。
        """
        provider = self._workdir_provider
        if provider is None:
            return 0
        workdir = provider()
        if workdir is None:
            return 0
        part_dir = Path(workdir) / ".downloading"
        if not part_dir.is_dir():
            return 0

        referenced = {
            os.path.normcase(os.path.abspath(p))
            for p in self._daos.downloads.resume_temp_paths()
        }
        now = time.time()
        removed = 0
        for f in part_dir.iterdir():
            if not f.is_file() or f.suffix != ".part":
                continue
            if os.path.normcase(os.path.abspath(str(f))) in referenced:
                continue
            if now - f.stat().st_mtime < _ORPHAN_MIN_AGE_S:
                continue
            SafeFileOps.safe_delete(f)
            removed += 1
        if removed:
            logger.info(".downloading 清理孤儿 .part %d 个", removed)
        return removed

    @staticmethod
    def _rebuild_payload(prow: ProcessTaskRow) -> dict[str, object]:
        """优先用 params 里存的全量 data 重建（一键重新处理保留全部参数）；
        旧版行 params 为空时退化为仅含 src 的最小 payload。"""
        import json

        if prow.params:
            try:
                data = json.loads(prow.params)
                if isinstance(data, dict) and data:
                    return {"type": prow.task_type, "data": data}
            except json.JSONDecodeError:
                logger.warning("process %s params 反序列化失败，退化为最小 payload",
                               prow.id)
        return {"type": prow.task_type,
                "data": {"items": [{"src": prow.src_path}]}}


