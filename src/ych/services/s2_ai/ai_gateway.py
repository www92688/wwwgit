# AI 网关：读「默认 AI 服务」配置 + keyring 密钥，组装远程调用。
# 同步阻塞 API——UI 调用方须放进工作线程（LlmWorker）。
from __future__ import annotations

import logging
from typing import Any

from ych.core.m1_capture.keyword_expander import build_prompts, parse_keywords
from ych.services.s2_ai.remote_provider import RemoteLLMClient
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.s2")


class AiGateway:
    """自定义 AI 服务统一入口；未配置时 is_configured()=False。"""

    def __init__(self, config: ConfigService, http: Any) -> None:
        self._config = config
        self._client = RemoteLLMClient(http)

    # ---- 配置 ----
    def default_service(self) -> dict[str, object] | None:
        """默认服务定义 {name, base_url, model, ...}；无/缺关键字段返回 None。"""
        services = self._config.get("ai_services")
        default_id = str(self._config.get("ai_default_service") or "")
        if not isinstance(services, dict) or default_id not in services:
            return None
        svc = services[default_id]
        if not isinstance(svc, dict):
            return None
        if not str(svc.get("base_url") or "") or not str(svc.get("model") or ""):
            return None
        return svc

    def is_configured(self) -> bool:
        return self.default_service() is not None

    def _api_key(self, service_id: str) -> str:
        return self._config.secret_get(f"ai:{service_id}")

    # ---- 能力 ----
    def list_models(
        self, service_id: str, base_url: str, api_key: str | None = None,
    ) -> list[str]:
        """api_key 为 None 时读已保存的 keyring 密钥（编辑已存服务场景）。"""
        return self._client.list_models(
            base_url, api_key if api_key is not None else self._api_key(service_id),
        )

    def expand_keywords(self, keyword: str, count: int = 10) -> list[str]:
        """围绕主词扩展搜索关键词（同步，放工作线程调用）。"""
        svc = self.default_service()
        if svc is None:
            from ych.common.errors import ERR_AI_REMOTE, AppError

            raise AppError(ERR_AI_REMOTE, "尚未配置默认 AI 服务，请到设置页添加")
        default_id = str(self._config.get("ai_default_service") or "")
        system, user = build_prompts(keyword, count)
        raw = self._client.chat(
            str(svc.get("base_url") or ""),
            self._api_key(default_id),
            str(svc.get("model") or ""),
            user,
            system_prompt=system,
        )
        words = parse_keywords(raw, keyword)
        logger.info("AI 关键词扩展：%s → %d 个", keyword, len(words))
        return words
