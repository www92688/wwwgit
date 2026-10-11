# AiGateway 默认服务判定：显式默认优先；唯一服务未设默认时自动视为默认
from __future__ import annotations

from typing import Any

import pytest

from ych.common.errors import AppError
from ych.services.s2_ai.ai_gateway import AiGateway

_SVC = {"name": "服务", "base_url": "https://api.example.com/v1", "model": "m-1"}


class _FakeHttp:
    """记录 post_json 入参，返回固定 OpenAI 格式响应供端到端断言。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def post_json(
        self, url: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append({
            "model": str((json_body or {}).get("model") or ""),
            "auth": str(headers or ""),
        })
        return {
            "choices": [
                {"message": {"content": "词一\n词二\n词三"}},
            ],
        }


def _gateway(config: Any, http: _FakeHttp | None = None) -> AiGateway:
    return AiGateway(config, http if http is not None else _FakeHttp())


# ---------- 默认服务判定 ----------
def test_no_service_unconfigured(memory_config) -> None:
    gw = _gateway(memory_config)
    assert gw.default_service_id() == ""
    assert gw.is_configured() is False


def test_sole_service_without_default_is_effective_default(memory_config) -> None:
    memory_config.set("ai_services", {"svc1": dict(_SVC)})
    gw = _gateway(memory_config)
    assert gw.default_service_id() == "svc1"
    assert gw.is_configured() is True
    svc = gw.default_service()
    assert svc is not None and svc["model"] == "m-1"


def test_explicit_default_wins(memory_config) -> None:
    memory_config.set(
        "ai_services",
        {"svc1": dict(_SVC), "svc2": dict(_SVC, model="m-2")},
    )
    memory_config.set("ai_default_service", "svc2")
    assert _gateway(memory_config).default_service_id() == "svc2"


def test_stale_default_falls_back_to_sole_service(memory_config) -> None:
    memory_config.set("ai_services", {"svc1": dict(_SVC)})
    memory_config.set("ai_default_service", "deleted-id")  # 指向已删服务的残留
    assert _gateway(memory_config).default_service_id() == "svc1"


def test_multi_services_without_default_unconfigured(memory_config) -> None:
    memory_config.set(
        "ai_services", {"svc1": dict(_SVC), "svc2": dict(_SVC, model="m-2")},
    )
    gw = _gateway(memory_config)
    assert gw.default_service_id() == ""
    assert gw.is_configured() is False


def test_sole_service_missing_model_unconfigured(memory_config) -> None:
    memory_config.set(
        "ai_services",
        {"svc1": {"name": "服务", "base_url": "https://x", "model": ""}},
    )
    assert _gateway(memory_config).is_configured() is False


# ---------- 关键词扩展走生效默认 ----------
def test_expand_uses_sole_service_and_its_key(
    memory_config, memory_keyring,
) -> None:
    memory_config.set("ai_services", {"svc1": dict(_SVC)})
    memory_config.secret_set("ai:svc1", "key-1")
    http = _FakeHttp()
    words = _gateway(memory_config, http).expand_keywords("爽文")
    assert words[:2] == ["词一", "词二"]
    assert http.calls[0]["model"] == "m-1"
    assert "key-1" in http.calls[0]["auth"]


def test_expand_without_service_raises(memory_config) -> None:
    with pytest.raises(AppError):
        _gateway(memory_config).expand_keywords("爽文")
