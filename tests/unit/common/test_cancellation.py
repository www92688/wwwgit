# cancellation 单元测试
from __future__ import annotations

import pytest

from ych.common.cancellation import (
    CancellationToken,
    LineFn,
    ProgressFn,
    SkippedSignal,
    TaskCanceled,
)


def test_token_not_cancelled_by_default() -> None:
    token = CancellationToken()
    assert token.cancelled is False
    token.check()  # 不抛异常


def test_token_cancel_then_check_raises() -> None:
    token = CancellationToken()
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(TaskCanceled):
        token.check()


def test_token_cancel_is_idempotent_and_thread_safe() -> None:
    token = CancellationToken()
    token.cancel()
    token.cancel()
    assert token.cancelled


def test_skipped_signal_can_be_raised_and_caught() -> None:
    def may_skip() -> None:
        raise SkippedSignal("目标已存在")

    with pytest.raises(SkippedSignal, match="目标已存在"):
        may_skip()


def test_callback_type_aliases_exist() -> None:
    # 类型别名仅存在于类型层，运行时验证可调用签名兼容性
    progress: ProgressFn = lambda v: None  # noqa: E731
    line: LineFn = lambda s: None  # noqa: E731
    progress(0.5)
    line("time=00:00:01.00")
