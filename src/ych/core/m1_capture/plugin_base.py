# 平台采集插件抽象基类（详设 12.1.1，扩展点一落地）
# 约定：插件一切网络行为经注入的 HttpClient（S4 唯一出口），不得自建 session
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

import requests

from ych.common.cancellation import CancellationToken, ProgressFn
from ych.common.errors import (
    ERR_PLG_KEY_INVALID,
    ERR_PLG_RATE_LIMITED,
    ERR_PLG_SCHEMA_CHANGED,
    ERR_PLG_UNAVAILABLE,
    AppError,
)
from ych.common.schemas import Region, ResumeState, SearchFilters, VideoMeta
from ych.services.s4_net.http_client import HttpClient
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m1")


def _as_int(v: object) -> int:
    """宽松取整：平台字段缺失或非数值时归零。"""
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _as_float(v: object) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


@runtime_checkable
class RateLimiterLike(Protocol):
    """S4 RateLimiter 的最小鸭子类型（便于测试注入空实现）。"""

    def set_rate(self, host: str, min_interval_s: float) -> None: ...

    def acquire(self, host: str, timeout_s: float = 10.0) -> None: ...


class PlatformPlugin(ABC):
    """平台插件契约（概要设计 5.1 接口的实现基类）。

    子类需提供类属性 id/display_name/region/requires_api_key/enabled_by_default，
    并实现 check_available/search；download 默认走 S4 直链流式下载。
    """

    id: str = ""
    display_name: str = ""
    region: Region = "cn"
    requires_api_key: bool = False
    enabled_by_default: bool = False
    # 国外总开关（foreign_platforms_enabled）是否约束本插件：
    # 免费素材站国内可直连不受约束；TikTok/YouTube 等受约束
    gated_by_foreign_master: bool = True

    # 限频参数：host 与最小请求间隔（秒）；空 host 表示不限频
    RATE_HOST: str = ""
    RATE_INTERVAL_S: float = 0.0

    def __init__(
        self,
        http: HttpClient,
        config: ConfigService,
        limiter: RateLimiterLike | None = None,
    ) -> None:
        self._http = http
        self._config = config
        self._limiter = limiter

    # ---- 抽象能力 ----
    @abstractmethod
    def check_available(self) -> tuple[bool, str]:
        """(可达?, 原因码)。必须 ≤5s 返回，任何情况下不得抛异常。"""
        ...

    @abstractmethod
    def search(
        self,
        keyword: str,
        filters: SearchFilters,
        max_count: int,
        token: CancellationToken | None,
    ) -> list[VideoMeta]:
        """关键词搜索；失败抛 AppError（PLG 域错误码）。"""
        ...

    # ---- 默认实现 ----
    def pick_download_variant(
        self, variants: list[dict[str, object]], filters: SearchFilters
    ) -> dict[str, object] | None:
        """默认清晰度选择：高度≥min_height 中体积最小者优先；无满足项返回 None。

        variants 各项约定含 link/width/height，可选 size 或 file_size（字节）；
        缺体积时以像素量(w*h)近似排序。
        """
        eligible = [
            v for v in variants
            if filters.min_height <= 0 or _as_int(v.get("height")) >= filters.min_height
        ]
        if not eligible:
            return None

        def _cost(v: dict[str, object]) -> float:
            size = v.get("size") or v.get("file_size")
            if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
                return float(size)
            w = _as_int(v.get("width"))
            h = _as_int(v.get("height"))
            return float(max(w * h, 1))

        return min(eligible, key=_cost)

    def download(
        self,
        meta: VideoMeta,
        dest_part: Path,
        on_progress: ProgressFn | None,
        resume: ResumeState | None,
        token: CancellationToken | None,
        on_state: Callable[[ResumeState], None] | None = None,
    ) -> ResumeState:
        """默认下载实现 = S4 download_stream 直链流式（支持 Range 续传）。

        on_state：周期性回调当前 ResumeState，调用方据此持久化断点。
        """
        self._rate_acquire()
        return self._http.download_stream(
            meta.download_url, dest_part, resume, on_progress, token,
            on_state=on_state,
        )

    # ---- 子类共用工具 ----
    def _rate_acquire(self) -> None:
        if self._limiter is None or not self.RATE_HOST or self.RATE_INTERVAL_S <= 0:
            return
        self._limiter.set_rate(self.RATE_HOST, self.RATE_INTERVAL_S)
        self._limiter.acquire(self.RATE_HOST)

    def api_get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: tuple[float, float] = (5.0, 30.0),
    ) -> requests.Response:
        """限频 + GET；网络异常统一映射 PLG010（不向协调器泄漏 NET 细节）。

        经 HttpClient._send 统一入口：本机代理死掉（Clash 关闭残留）时
        自动直连兜底，可直连的免费素材站不因死代理整体不可用。
        """
        self._rate_acquire()
        try:
            return self._http.send(
                "get", url, params=params, headers=headers, timeout=timeout
            )
        except requests.exceptions.Timeout as exc:
            raise AppError(ERR_PLG_UNAVAILABLE, "平台连接超时", cause=exc) from exc
        except requests.exceptions.RequestException as exc:
            raise AppError(ERR_PLG_UNAVAILABLE, "平台连接失败", cause=exc) from exc

    @staticmethod
    def raise_for_status(resp: requests.Response) -> None:
        """HTTP 状态 → PLG 域业务异常（401/403→PLG002、429→PLG003、其余→PLG010）。"""
        if resp.status_code == 200:
            return
        if resp.status_code in (401, 403):
            raise AppError(ERR_PLG_KEY_INVALID, "API Key 无效或未授权")
        if resp.status_code == 429:
            raise AppError(ERR_PLG_RATE_LIMITED, "触发平台限频，请稍后重试")
        raise AppError(ERR_PLG_UNAVAILABLE, f"平台响应异常（HTTP {resp.status_code}）")

    @staticmethod
    def parse_json(resp: requests.Response, source: str) -> dict[str, object]:
        """响应 JSON 解析；结构非法抛 PLG020（接口变更预警）。"""
        try:
            data = resp.json()
        except ValueError as exc:
            raise AppError(ERR_PLG_SCHEMA_CHANGED, f"{source} 响应解析失败", cause=exc) from exc
        if not isinstance(data, dict):
            raise AppError(ERR_PLG_SCHEMA_CHANGED, f"{source} 响应结构已变更")
        return data


class SkeletonPlugin(PlatformPlugin):
    """六平台首版统一骨架占位（详设 12.1.5）：UI 显示灰色"暂不可用"，不阻塞他台。"""

    NOT_OPEN_MSG = "该平台暂未开放采集，敬请期待后续版本"

    def check_available(self) -> tuple[bool, str]:
        return (False, "not_implemented")

    def search(
        self,
        keyword: str,
        filters: SearchFilters,
        max_count: int,
        token: CancellationToken | None,
    ) -> list[VideoMeta]:
        raise AppError(ERR_PLG_UNAVAILABLE, self.NOT_OPEN_MSG)

    def download(
        self,
        meta: VideoMeta,
        dest_part: Path,
        on_progress: ProgressFn | None,
        resume: ResumeState | None,
        token: CancellationToken | None,
        on_state: Callable[[ResumeState], None] | None = None,
    ) -> ResumeState:
        raise AppError(ERR_PLG_UNAVAILABLE, self.NOT_OPEN_MSG)
