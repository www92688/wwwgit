# S2 远程 LLM 客户端单元测试（responses mock，不触网）
from __future__ import annotations

import pytest
import responses

from ych.common.errors import ERR_AI_REMOTE, AppError
from ych.services.s2_ai.remote_provider import RemoteLLMClient
from ych.services.s4_net.http_client import HttpClient


@pytest.fixture
def client(memory_config) -> RemoteLLMClient:
    return RemoteLLMClient(HttpClient(memory_config))


# ---------- 模型列表 ----------
def test_list_models_without_v1_suffix(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET, "https://relay.example.com/v1/models",
            json={"data": [{"id": "gpt-4o"}, {"id": "deepseek-v3"}]},
            status=200,
        )
        models = client.list_models("https://relay.example.com", "sk-test")
    assert models == ["deepseek-v3", "gpt-4o"]


def test_list_models_keeps_explicit_v1(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET, "https://relay.example.com/v1/models",
            json={"data": [{"id": "m1"}]}, status=200,
        )
        models = client.list_models("https://relay.example.com/v1/", "sk-test")
    assert models == ["m1"]


def test_list_models_keeps_provider_version_prefix(client) -> None:
    # 智谱 /paas/v4、火山 /api/v3 等多版本前缀不得被二次追加 /v1
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET, "https://open.bigmodel.cn/api/paas/v4/models",
            json={"data": [{"id": "glm-4"}]}, status=200,
        )
        models = client.list_models(
            "https://open.bigmodel.cn/api/paas/v4", "sk-test",
        )
    assert models == ["glm-4"]


def test_list_models_unauthorized_carries_body(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET, "https://relay.example.com/v1/models",
            body='{"error": {"message": "invalid api key"}}', status=401,
        )
        with pytest.raises(AppError) as exc:
            client.list_models("https://relay.example.com", "bad-key")
    assert exc.value.code == ERR_AI_REMOTE
    assert "invalid api key" in str(exc.value)


def test_list_models_empty_base_url(client) -> None:
    with pytest.raises(AppError) as exc:
        client.list_models("  ", "sk")
    assert exc.value.code == ERR_AI_REMOTE


# ---------- 对话补全 ----------
def test_chat_returns_first_message(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST, "https://relay.example.com/v1/chat/completions",
            json={"choices": [{"message": {"role": "assistant",
                                           "content": " 地毯清洗教程 "}}]},
            status=200,
        )
        out = client.chat(
            "https://relay.example.com", "sk", "gpt-4o", "扩展关键词",
            system_prompt="你是助手",
        )
    assert out == "地毯清洗教程"


def test_chat_requires_model(client) -> None:
    with pytest.raises(AppError) as exc:
        client.chat("https://relay.example.com", "sk", "  ", "hi")
    assert exc.value.code == ERR_AI_REMOTE


def test_chat_malformed_response(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST, "https://relay.example.com/v1/chat/completions",
            json={"choices": []}, status=200,
        )
        with pytest.raises(AppError) as exc:
            client.chat("https://relay.example.com", "sk", "m", "hi")
    assert exc.value.code == ERR_AI_REMOTE
