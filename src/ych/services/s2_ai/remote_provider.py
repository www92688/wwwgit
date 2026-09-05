# 远程 LLM 提供方（OpenAI 兼容 /v1/models、/v1/chat/completions）。
# 供自定义 AI 服务（中转站/官方 API）调用；传输统一走 HttpClient（代理/重试/日志）。
from __future__ import annotations

import re

from ych.common.errors import ERR_AI_REMOTE, AppError
from ych.services.s4_net.http_client import HttpClient

_AUTH_HEADER = "Authorization"

# 已含版本前缀的地址（/v1 /v3 /v4、/api/v3、/paas/v4、/compatible-mode/v1…）
# 按原样使用；否则自动补 /v1
_VERSION_SUFFIX = re.compile(r"/v\d+$")


def _endpoint(base_url: str, path: str) -> str:
    """规范接口地址：无版本后缀自动补 /v1（如 https://api.x.com → …/v1/models）。"""
    base = base_url.strip().rstrip("/")
    if not base:
        raise AppError(ERR_AI_REMOTE, "接口地址为空，请先在设置页填写")
    if not _VERSION_SUFFIX.search(base):
        base = f"{base}/v1"
    return f"{base}{path}"


def _auth(api_key: str) -> dict[str, str]:
    if not api_key.strip():
        raise AppError(ERR_AI_REMOTE, "API Key 为空，请先在设置页填写")
    return {_AUTH_HEADER: f"Bearer {api_key.strip()}"}


class RemoteLLMClient:
    """最小 OpenAI 兼容客户端：只需 models 列表与 chat 补全两个能力。"""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def list_models(self, base_url: str, api_key: str) -> list[str]:
        """GET /v1/models → 模型 id 列表（升序）。"""
        data = self._http.get_json(
            _endpoint(base_url, "/models"),
            headers=_auth(api_key),
            status_error_code=ERR_AI_REMOTE,
        )
        if not isinstance(data, dict):
            raise AppError(ERR_AI_REMOTE, "模型列表响应格式异常")
        raw = data.get("data")
        if not isinstance(raw, list):
            raise AppError(ERR_AI_REMOTE, "模型列表响应缺少 data 字段")
        models: list[str] = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                models.append(item["id"])
        return sorted(models)

    def chat(
        self,
        base_url: str,
        api_key: str,
        model: str,
        user_prompt: str,
        system_prompt: str = "",
        timeout_s: float = 60.0,
    ) -> str:
        """POST /v1/chat/completions → 首个回复文本。"""
        if not model.strip():
            raise AppError(ERR_AI_REMOTE, "未选择模型，请先在设置页拉取或填写")
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        data = self._http.post_json(
            _endpoint(base_url, "/chat/completions"),
            json_body={
                "model": model.strip(),
                "messages": messages,
                "stream": False,
            },
            headers=_auth(api_key),
            timeout_s=timeout_s,
        )
        if not isinstance(data, dict):
            raise AppError(ERR_AI_REMOTE, "对话响应格式异常")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AppError(ERR_AI_REMOTE, "对话响应缺少 choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise AppError(ERR_AI_REMOTE, "对话响应 choices 格式异常")
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise AppError(ERR_AI_REMOTE, "对话响应内容为空")
        return content.strip()
