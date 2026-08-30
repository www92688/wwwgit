# RateLimiter 假时钟节奏测试（对照 6.4）
from __future__ import annotations

from ych.services.s4_net.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _sleep_and_advance(clock: FakeClock, records: list[float]):
    def sleep(seconds: float) -> None:
        records.append(seconds)
        clock.now += seconds

    return sleep


def test_rate_limiter_rhythm_with_fake_clock() -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    rl = RateLimiter(clock=clock, sleep_fn=_sleep_and_advance(clock, sleeps))
    rl.set_rate("api.pexels.com", min_interval_s=18)

    rl.acquire("api.pexels.com", timeout_s=60.0)   # 首次立即放行
    assert clock.now == 100.0
    assert sleeps == []

    rl.acquire("api.pexels.com", timeout_s=60.0)   # 应等 18s
    assert sleeps == [18.0]
    assert clock.now == 118.0

    rl.acquire("api.pexels.com", timeout_s=60.0)   # 再等 18s
    assert sleeps == [18.0, 18.0]
    assert clock.now == 136.0


def test_rate_limiter_unlimited_host_no_sleep() -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    rl = RateLimiter(clock=clock, sleep_fn=_sleep_and_advance(clock, sleeps))
    for _ in range(5):
        rl.acquire("fast.host")
    assert sleeps == []
    assert clock.now == 100.0


def test_rate_limiter_timeout_caps_wait() -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    rl = RateLimiter(clock=clock, sleep_fn=_sleep_and_advance(clock, sleeps))
    rl.set_rate("slow.host", min_interval_s=100)
    rl.acquire("slow.host")
    rl.acquire("slow.host", timeout_s=5.0)   # 等待被 timeout 截断后放行
    assert sleeps == [5.0]
