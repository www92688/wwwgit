# 对比候选缓存（详设 14.3）：<用户数据目录>/compare_cache/<plugin>/<key>.mp4，TTL 惰性清理
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from ych.common.cancellation import CancellationToken
from ych.common.errors import ERR_DL_WRITE_FAILED, AppError
from ych.common.schemas import VideoMeta

logger = logging.getLogger("ych.m3")


def user_data_dir() -> Path:
    """Windows %LOCALAPPDATA%\\YuChongGou；其他平台 ~/.yu_chong_gou。"""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "YuChongGou"
    return Path.home() / ".yu_chong_gou"


# 下载回调：由 CandidateSearcher 注入（绑定具体插件），返回含数据的最终临时文件
Downloader = Callable[[VideoMeta, Path, CancellationToken | None], Path]


class CandidateCache:
    """候选视频磁盘缓存；TTL 到期惰性删除（不后台扫描）。"""

    def __init__(
        self,
        downloader: Downloader,
        root: Path | None = None,
        ttl_days: float = 7.0,
    ) -> None:
        self._download = downloader
        self._root = root if root is not None else (
            user_data_dir() / "compare_cache"
        )
        self._ttl_s = ttl_days * 86400.0

    def path_for(self, plugin_id: str, video_key: str) -> Path:
        safe_key = "".join(c for c in video_key if c.isalnum() or c in "-_")
        return self._root / plugin_id / f"{safe_key or 'unknown'}.mp4"

    def fetch_or_download(
        self,
        meta: VideoMeta,
        token: CancellationToken | None = None,
    ) -> Path:
        """命中且未过期直接返回；过期即删；未命中走下载（.part → 原子落位）。"""
        self._lazy_cleanup()
        target = self.path_for(meta.plugin_id, meta.video_key)
        if target.exists():
            age = time.time() - target.stat().st_mtime
            if age <= self._ttl_s:
                return target
            logger.info("候选缓存过期：%s", target.name)
            target.unlink(missing_ok=True)

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        try:
            final = self._download(meta, tmp, token)
            os.replace(final, target)
        except Exception as exc:
            raise AppError(ERR_DL_WRITE_FAILED,
                           f"候选下载失败：{meta.video_key}", cause=exc) from exc
        finally:
            tmp.unlink(missing_ok=True)
        return target

    # ---- 内部 ----
    def _lazy_cleanup(self) -> None:
        if not self._root.exists():
            return
        now = time.time()
        for f in self._root.glob("*/*"):
            try:
                if now - f.stat().st_mtime > self._ttl_s:
                    f.unlink()
                    logger.info("TTL 清理：%s", f)
            except OSError:
                continue
