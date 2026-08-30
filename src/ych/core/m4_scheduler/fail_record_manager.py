# 失败记录管理（详设 11.3）：统一入失败列表 + 一键重建任务
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from ych.common.errors import AppError
from ych.common.schemas import TaskPayload, VideoMeta
from ych.services.s3_db.daos import FailRecordDao

logger = logging.getLogger("ych.m4")


class FailRecordManager:
    """失败统一落 fail_record（文件名/原因/时间/类型/全量重建 payload）。"""

    def __init__(self, fails: FailRecordDao) -> None:
        self._fails = fails

    def record(self, task: ManagedTaskLike, err: Exception) -> int:
        p: TaskPayload = task.payload
        file_name = self._source_name(p)
        reason = err.message if isinstance(err, AppError) else str(err)
        code = err.code if isinstance(err, AppError) else "UNKNOWN"
        rid = self._fails.add(
            file_name=file_name,
            reason=reason,
            code=code,
            task_type=p.type,
            payload=self.rebuild_info(p),
        )
        logger.info("fail recorded #%s %s [%s]", rid, file_name, code)
        return rid

    @staticmethod
    def _source_name(payload: TaskPayload) -> str:
        if payload.type == "download":
            metas = [m for m in payload.data.get("metas") or []  # type: ignore[attr-defined]
                     if isinstance(m, VideoMeta)]
            if metas:
                meta: VideoMeta = metas[0]
                return meta.title or f"{meta.plugin_id}_{meta.video_key}.mp4"
            return "download.mp4"
        items = list(payload.data.get("items") or [])  # type: ignore[call-overload]
        if items:
            return Path(str(items[0].get("src", "?"))).name
        srcs = list(payload.data.get("srcs") or ["?"])  # type: ignore[call-overload]
        return Path(str(srcs[0])).name

    @staticmethod
    def rebuild_info(payload: TaskPayload) -> dict[str, object]:
        """payload 全量 JSON 化（dataclass → dict），供一键重新处理。"""
        data: dict[str, object] = dict(payload.data)
        raw_metas = data.get("metas")
        metas: list[VideoMeta] = (
            [m for m in raw_metas if isinstance(m, VideoMeta)]
            if isinstance(raw_metas, list) else []
        )
        if metas:
            data["metas"] = [asdict(m) for m in metas]
        return {"type": payload.type, "data": data}

    def rebuild_payload(self, record_id: int) -> TaskPayload:
        """从失败记录行重建 TaskPayload。"""
        row = self._fails.get(record_id)
        if row is None:
            raise ValueError(f"失败记录不存在：{record_id}")
        info = json.loads(row.payload)
        data = info.get("data", {})
        if info.get("type") == "download" and data.get("metas"):
            data["metas"] = [VideoMeta(**m) for m in data["metas"]]
        return TaskPayload(type=info.get("type", "preprocess"), data=data)







class ManagedTaskLike(Protocol):
    payload: TaskPayload

