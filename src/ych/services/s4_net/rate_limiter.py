# 按 host 的令牌桶限速器（详设 6.2）；时钟与睡眠可注入，便于假时钟测试
from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    """每个 host 独立的最小请求间隔控制（令牌桶简化形态）。

    插件按平台配额调用 set_rate（如 Pexels 1 req/18s）；
    acquire 阻塞至该 host 下一次允许请求的时刻。
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep_fn
        # host -> 最小间隔秒数
        self._intervals: dict[str, float] = {}
        # host -> 下次允许请求的时刻
        self._next_allowed: dict[str, float] = {}

    def set_rate(self, host: str, min_interval_s: float) -> None:
        """设置 host 的最小请求间隔（秒）。"""
        self._intervals[host] = float(min_interval_s)

    def acquire(self, host: str, timeout_s: float = 10.0) -> None:
        """获取该 host 的请求许可；超过 timeout_s 未获得则静默放行。

        （放行而非抛错：限频的硬约束由服务端 429 兜底，见 PLG003）
        """
        now = self._clock()
        interval = self._intervals.get(host, 0.0)
        next_at = self._next_allowed.get(host, now)
        wait = next_at - now
        if wait > 0:
            if wait <= timeout_s:
                self._sleep(wait)
            else:
                self._sleep(timeout_s)
            now = self._clock()
        if interval > 0:
            self._next_allowed[host] = max(now, next_at) + interval
