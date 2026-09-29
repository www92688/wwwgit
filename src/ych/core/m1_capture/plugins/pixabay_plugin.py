# Pixabay 插件完整实现（详设 12.1.4）
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

# 官方配额随档位变化，按保守 1 req/2s（SP-6 实测后回填）
_RATE_INTERVAL_S = 2.0

_TIERS = ("large", "medium", "small", "tiny")


class PixabayPlugin(PlatformPlugin):
    id = "pixabay"
    display_name = "Pixabay"
    region = "global"
    requires_api_key = True
    enabled_by_default = True
    # 国内可直连的免费素材站：不受「国外平台」总开关约束
    gated_by_foreign_master = False

    RATE_HOST = "pixabay.com"
    RATE_INTERVAL_S = _RATE_INTERVAL_S

    API_BASE = "https://pixabay.com/api/videos"

    # ---- Key ----
    def _api_key(self) -> str:
        return self._config.secret_get("pixabay")

    # ---- 可用性检查：200 含 hits→可用；400 含 invalid key→PLG002 ----
    def check_available(self) -> tuple[bool, str]:
        key = self._api_key()
        if not key:
            return (False, ERR_PLG_KEY_MISSING)
        try:
            resp = self.api_get(
                f"{self.API_BASE}/",
                params={"key": key, "q": "test", "per_page": "3"},
                timeout=(5.0, 5.0),
            )
        except AppError as exc:
            return (False, exc.code)
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                return (False, "PLG020")
            if isinstance(data, dict) and "hits" in data:
                return (True, "ok")
            return (False, "PLG010")
        if resp.status_code == 400 and "invalid key" in resp.text.lower():
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
            raise AppError(ERR_PLG_KEY_MISSING, "尚未配置 Pixabay API Key，请在设置页填写")
        if "://" in keyword or len(keyword) > 100:
            # 链接不是合理的素材搜索词：API 会 400，直接视为无结果
            # （采集页粘贴抖音主页链接时，素材站不应报"平台不可用"）
            return []
        resp = self.api_get(
            f"{self.API_BASE}/",
            params={"key": key, "q": keyword, "per_page": str(min(max_count, 50))},
        )
        if token is not None:
            token.check()
        self.raise_for_status(resp)
        data = self.parse_json(resp, "Pixabay")

        items: list[VideoMeta] = []
        hits = data.get("hits") or []
        if not isinstance(hits, list):
            raise AppError("PLG020", "Pixabay 响应结构已变更（hits）")
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            item = self._map_hit(cast(dict[str, object], hit), filters)
            if item is not None:
                items.append(item)
        logger.info("Pixabay 搜索「%s」命中 %d 条（筛选后）", keyword, len(items))
        return items

    def _map_hit(self, hit: dict[str, object], filters: SearchFilters) -> VideoMeta | None:
        variants = self._expand_variants(hit.get("videos"))
        chosen = self.pick_download_variant(variants, filters)
        if chosen is None or not chosen.get("link"):
            return None
        picture_id = str(hit.get("picture_id") or "")
        thumbnail = (
            f"https://i.vimeocdn.com/video/{picture_id}_640.jpg" if picture_id else ""
        )
        views = hit.get("views")
        extra: dict[str, object] = {"tier": str(chosen.get("tier") or "")}
        if isinstance(views, int):
            extra["views"] = views
        size = chosen.get("size")
        return VideoMeta(
            plugin_id=self.id,
            video_key=str(hit.get("id") or ""),
            title=str(hit.get("tags") or ""),
            page_url=str(hit.get("pageURL") or ""),
            duration_s=_as_float(hit.get("duration")),
            width=_as_int(chosen.get("width")),
            height=_as_int(chosen.get("height")),
            file_size_bytes=(
                int(size) if isinstance(size, (int, float))
                and not isinstance(size, bool) and size > 0 else None
            ),
            watermark_tag="no",          # 官方素材站内容无第三方水印
            download_url=str(chosen.get("link")),
            thumbnail_url=thumbnail,
            extra=extra,
        )

    @staticmethod
    def _expand_variants(raw: object) -> list[dict[str, object]]:
        """videos 字典 large/medium/small/tiny 展开为变体列表；tiny 的 gif 过滤。"""
        if not isinstance(raw, dict):
            return []
        variants: list[dict[str, object]] = []
        for tier in _TIERS:
            ent = raw.get(tier)
            if not isinstance(ent, dict):
                continue
            url = str(ent.get("url") or "")
            if not url or Path(url.split("?")[0]).suffix.lower() == ".gif":
                continue   # tiny 档常为 gif，按扩展名过滤（详设 12.1.4）
            variants.append({
                "tier": tier,
                "link": url,
                "width": ent.get("width"),
                "height": ent.get("height"),
                "size": ent.get("size"),
            })
        return variants

    # ---- 下载：videos 字典内直链 GET（默认实现即可）----
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
