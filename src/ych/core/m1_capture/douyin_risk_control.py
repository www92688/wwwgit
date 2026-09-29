# 抖音风控节流（方案一）：冷却期 / 每日预算 / 最小搜索间隔
# 设计要点：
# - 状态经 ConfigService 持久化（JSON 值），应用重启后冷却仍然生效；
# - 确定性拒绝（平台安全校验类）当场不再重试——重试本身只会延长封锁；
# - 冷却时长按连续拒绝次数阶梯放大，采集成功后清零；
# - 本模块不依赖 Qt，纯逻辑便于单测；UI 只读（read_* 帮助函数）。
from __future__ import annotations

import time
from typing import Any

# 连续被拒次数（1 起）→ 冷却秒数；封顶 24h，采集成功即清零
_COOLDOWN_LADDER_S = (45 * 60, 2 * 3600, 6 * 3600, 24 * 3600)
DEFAULT_BUDGET = 20          # 每日搜索预算（次）
WARN_BUDGET_REMAIN = 5       # 剩余 ≤ 该值时提醒
MIN_SEARCH_INTERVAL_S = 90.0  # 两次搜索最小间隔

# CLI 输出中的确定性拒绝特征（平台原话，勿翻译；命中即不再重试）
_REJECT_MARKERS = ("被抖音拒绝", "安全校验", "风控验证", "未返回作品详情")


def rejection_in_output(tail: list[str]) -> bool:
    """CLI 输出尾部是否含平台确定性拒绝（这类拒绝重试/重登都无效）。"""
    joined = "\n".join(tail)
    return any(marker in joined for marker in _REJECT_MARKERS)


def cooldown_remaining(config: Any, clock: Any = time.time) -> float:
    """冷却剩余秒数；未在冷却中返回 0。UI 与核心共用。"""
    until = float(_cfg_get(config, "douyin_cooldown_until") or 0)
    return max(0.0, until - clock())


def cooldown_hint(remaining_s: float, clock: Any = time.time) -> str:
    """剩余秒数 → 人话（"约 45 分钟后可再试"）；不在冷却返回空串。"""
    if remaining_s <= 0:
        return ""
    mins = int(remaining_s // 60) + 1
    if mins >= 60:
        return f"约 {mins // 60} 小时 {mins % 60} 分后可再试"
    return f"约 {mins} 分钟后可再试"


def budget_state(config: Any, budget: int = DEFAULT_BUDGET, clock: Any = time.time) -> tuple[int, int]:
    """(今日已用, 上限)；跨自然日自动归零。"""
    raw = _cfg_get(config, "douyin_daily_usage")
    today = time.strftime("%Y-%m-%d", time.localtime(clock()))
    if not isinstance(raw, dict) or raw.get("date") != today:
        return 0, budget
    return int(raw.get("used") or 0), budget


class DouyinRiskControl:
    """插件侧状态机：查冷却/间隔/预算 → 消耗 → 记录拒绝/成功。"""

    def __init__(
        self,
        config: Any,
        clock: Any = time.time,
        budget: int = DEFAULT_BUDGET,
        min_interval_s: float = MIN_SEARCH_INTERVAL_S,
    ) -> None:
        self._config = config
        self._clock = clock
        self._budget = budget
        self._min_interval_s = min_interval_s

    # ---- 查询 ----
    def cooldown_remaining(self) -> float:
        return cooldown_remaining(self._config, self._clock)

    def cooldown_hint(self) -> str:
        return cooldown_hint(self.cooldown_remaining(), self._clock)

    def search_too_soon(self) -> float:
        """距上次搜索过近时返回需等待的秒数；否则 0。"""
        last = float(_cfg_get(self._config, "douyin_last_search_ts") or 0)
        return max(0.0, self._min_interval_s - (self._clock() - last))

    def budget_exhausted(self) -> bool:
        used, limit = budget_state(self._config, self._budget, self._clock)
        return used >= limit

    # ---- 变更 ----
    def note_search_start(self) -> tuple[int, int]:
        """搜索真正发起前调用：刷新最近时间戳并消耗 1 次预算。"""
        self._config.set("douyin_last_search_ts", self._clock())
        used, limit = budget_state(self._config, self._budget, self._clock)
        self._config.set(
            "douyin_daily_usage", {"date": self._today(), "used": used + 1},
        )
        return used + 1, limit

    def note_rejection(self) -> str:
        """确定性拒绝：按连续次数放大冷却，返回人话提示。"""
        strikes = int(_cfg_get(self._config, "douyin_reject_strikes") or 0) + 1
        idx = min(strikes - 1, len(_COOLDOWN_LADDER_S) - 1)
        self._config.set("douyin_reject_strikes", strikes)
        self._config.set("douyin_cooldown_until", self._clock() + _COOLDOWN_LADDER_S[idx])
        return self.cooldown_hint()

    def note_success(self) -> None:
        """采集成功：连续拒绝计数清零（冷却自然到期，不主动缩短）。"""
        if _cfg_get(self._config, "douyin_reject_strikes"):
            self._config.set("douyin_reject_strikes", 0)

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._clock()))


def _cfg_get(config: Any, key: str) -> Any:
    try:
        return config.get(key)
    except Exception:
        return None
