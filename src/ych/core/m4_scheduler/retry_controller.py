# 重试控制器（详设 11.3）：可重试错误码集合 + [2,8] 指数退避
from __future__ import annotations

import logging
from typing import Protocol

from ych.common.errors import (
    ERR_AI_INFER_TIMEOUT,
    ERR_DL_VERIFY_FAILED,
    ERR_DL_WRITE_FAILED,
    ERR_MED_TRANSCODE_FAILED,
    ERR_PLG_RATE_LIMITED,
    AppError,
)
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m4")

# 可重试集合：NET*、DL001/DL002、MED010、AI004、PLG003（详设 11.3）
RETRYABLE_PREFIXES = ("NET",)
RETRYABLE_EXACT = {
    ERR_DL_WRITE_FAILED,      # DL001
    ERR_DL_VERIFY_FAILED,     # DL002
    ERR_MED_TRANSCODE_FAILED,  # MED010
    ERR_AI_INFER_TIMEOUT,     # AI004
    ERR_PLG_RATE_LIMITED,     # PLG003
}


def is_retryable(code: str) -> bool:
    return code.startswith(RETRYABLE_PREFIXES) or code in RETRYABLE_EXACT




class RetryController:
    def __init__(self, config: ConfigService) -> None:
        self._config = config

    def should_retry(self, task: ManagedTaskLike, err: Exception) -> bool:
        """AppError 且 code ∈ 可重试集合 且 retry_count < max_retry。"""
        if not isinstance(err, AppError):
            return False
        max_retry = self._config.get_typed("max_retry", int)
        if task.retry_count >= max_retry:
            return False
        return is_retryable(err.code)

    def backoff_seconds(self, retry_index: int) -> float:
        """第 i 次重试延迟取 retry_backoff_seconds[i]，越界取末值。"""
        raw = self._config.get("retry_backoff_seconds") or [2, 8]
        backoff: list[float] = [float(x) for x in raw]  # type: ignore[attr-defined]
        idx = min(max(retry_index, 0), len(backoff) - 1)
        try:
            delay = float(backoff[idx])
        except (TypeError, ValueError):
            delay = 2.0
        return delay



class ManagedTaskLike(Protocol):
    retry_count: int




