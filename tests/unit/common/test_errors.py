# errors 单元测试
from __future__ import annotations

import pytest

from ych.common.errors import (
    ERR_AI_MODEL_MISSING,
    ERR_DB_CONSTRAINT,
    ERR_NET_TIMEOUT,
    ERR_PLG_UNAVAILABLE,
    ERR_TASK_CANCELED,
    AppError,
)


def test_app_error_carries_code_and_message() -> None:
    err = AppError(ERR_NET_TIMEOUT, "连接超时，请检查网络")
    assert err.code == ERR_NET_TIMEOUT
    assert err.message == "连接超时，请检查网络"
    assert "NET001" in str(err)
    assert "连接超时" in str(err)


def test_app_error_with_cause() -> None:
    cause = ValueError("底层错误")
    err = AppError(ERR_DB_CONSTRAINT, "唯一约束冲突", cause=cause)
    assert err.cause is cause
    assert "DB003" in str(err)


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        (ERR_AI_MODEL_MISSING, "AI001"),
        (ERR_PLG_UNAVAILABLE, "PLG010"),
        (ERR_TASK_CANCELED, "TASK004"),
    ],
)
def test_error_codes_values(code: str, needle: str) -> None:
    assert code == needle
