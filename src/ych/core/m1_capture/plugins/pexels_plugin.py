# Pexels 插件完整实现（详设 12.1.3）
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ych.common.cancellation import CancellationToken, ProgressFn
from ych.common.errors import (
    ERR_PLG_KEY_INVALID,
    ERR_PLG_KEY_MISSING,
    AppError,
)
from ych.common.schemas import ResumeState, SearchFilters, VideoMeta
from ych.core.m1_capture.plugin_base import PlatformPlugin, _as_float, _as_int

logger = logging.getLogger("ych.m1")

# 免费档约 200 req/h → 1 req/18s（详设 12.1.3；429 由 PLG003 兜底）
_RATE_INTERVAL_S = 18.0


class PexelsPlugin(PlatformPlugin):
    id = "pexels"
    display_name = "Pexels"
    region = "global"
    requires_api_key = True
    enabled_by_default = True
    # 国内可直连的免费素材站：不受「国外平台」总开关约束
    gated_by_foreign_master = False

    RATE_HOST = "api.pexels.com"
    RATE_INTERVAL_S = _RATE_INTERVAL_S

    API_BASE = "https://api.pexels.com"

    # ---- Key ----
    def _api_key(self) -> str:
        # keyring 密钥名与 S5 secret_set 标记联动约定为 "pexels"
        return self._config.secret_get("pexels")

    # ---- 可用性检查：200→可用、401/403→PLG002、超时→PLG010 ----
    def check_available(self) -> tuple[bool, str]:
        key = self._api_key()
        if not key:
            return (False, ERR_PLG_KEY_MISSING)
        try:
            resp = self.api_get(
                f"{self.API_BASE}/videos/search",
                params={"query": "test", "per_page": "1"},
                headers={"Authorization": key},
                timeout=(5.0, 5.0),
            )
        except AppError as exc:
            return (False, exc.code)
        if resp.status_code == 200:
            return (True, "ok")
        if resp.status_code in (401, 403):
            return (False, ERR_PLG_KEY_INVALID)
        return (False, "PLG010")

    # ---- 搜索映射 ----
    def search(
        self,
        keyword: str,
        filters: SearchFilters,
        max_count: int,
        token: CancellationToken | None,
    ) -> list[VideoMeta]:
        key = self._api_key()
        if not key:
            raise AppError(ERR_PLG_KEY_MISSING, "尚未配置 Pexels API Key，请在设置页填写")
        resp = self.api_get(
            f"{self.API_BASE}/videos/search",
            params={
                "query": keyword,
                "per_page": str(min(max_count, 80)),
                "page": "1",
            },
            headers={"Authorization": key},
        )
        if token is not None:
            token.check()
        self.raise_for_status(resp)
        data = self.parse_json(resp, "Pexels")

        items: list[VideoMeta] = []
        videos = data.get("videos") or []
        if not isinstance(videos, list):
            raise AppError("PLG020", "Pexels 响应结构已变更（videos）")
        for v in videos:
            if not isinstance(v, dict):
                continue
            item = self._map_video(cast(dict[str, object], v), filters)
            if item is not None:
                items.append(item)
        logger.info("Pexels 搜索「%s」命中 %d 条（筛选后）", keyword, len(items))
        return items

    def _map_video(self, v: dict[str, object], filters: SearchFilters) -> VideoMeta | None:
        raw_files = v.get("video_files") or []
        if not isinstance(raw_files, list):
            return None
        mp4_files = [
            cast(dict[str, object], f) for f in raw_files
            if isinstance(f, dict) and f.get("file_type") == "video/mp4"
        ]
        chosen = self.pick_download_variant(mp4_files, filters)
        if chosen is None:
            return None   # 无满足清晰度要求的候选 → 本地过滤掉
        size = chosen.get("file_size") or chosen.get("size")
        user = v.get("user")
        title = ""
        if isinstance(user, dict):
            title = str(user.get("name") or "")
        return VideoMeta(
            plugin_id=self.id,
            video_key=str(v.get("id") or ""),
            title=title,
            page_url=str(v.get("url") or ""),
            duration_s=_as_float(v.get("duration")),
            width=_as_int(v.get("width")) or _as_int(chosen.get("width")),
            height=_as_int(v.get("height")) or _as_int(chosen.get("height")),
            file_size_bytes=(
                int(size) if isinstance(size, (int, float))
                and not isinstance(size, bool) and size > 0 else None
            ),
            watermark_tag="no",          # 官方素材无第三方水印（详设 12.1.3）
            download_url=str(chosen.get("link") or ""),
            thumbnail_url=str(v.get("image") or ""),
            extra={"quality": str(chosen.get("quality") or "")},
        )

    # ---- 下载：标准 GET 直链，支持 Range（默认实现即可）----
    def download(
        self,
        meta: VideoMeta,
        dest_part: Path,
        on_progress: ProgressFn | None,
        resume: ResumeState | None,
        token: CancellationToken | None,
        on_state: Callable[[ResumeState], None] | None = None,
    ) -> ResumeState:
        return super().download(meta, dest_part, on_progress, resume, token,
                                on_state=on_state)
