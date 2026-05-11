"""LLM 客户端封装：延迟初始化 OpenAI SDK，并暴露运行状态。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .config import Settings


class OpenAILLMClient:
    """OpenAI Chat Completions 客户端的轻量包装。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.openai_model
        self.enabled = bool(settings.openai_enabled and settings.openai_api_key)
        self._client: Optional[Any] = None
        self._init_error: Optional[str] = None

    def _get_client(self) -> Optional[Any]:
        """按需创建 SDK 客户端；配置缺失或初始化失败时返回 None。"""
        if not self.enabled:
            return None
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            self._init_error = "The openai package is not installed. Run pip install -r requirements.txt."
            self.enabled = False
            return None

        kwargs: Dict[str, Any] = {
            "api_key": self.settings.openai_api_key,
            "timeout": self.settings.openai_timeout_seconds,
        }
        if self.settings.openai_base_url:
            kwargs["base_url"] = self.settings.openai_base_url
        try:
            self._client = OpenAI(**kwargs)
        except Exception as exc:  # pragma: no cover - defensive SDK initialization guard
            self._init_error = str(exc)
            self.enabled = False
            return None
        return self._client

    def status(self) -> Dict[str, Any]:
        """返回给健康检查和审计日志使用的 LLM 配置摘要。"""
        return {
            "enabled": self.enabled,
            "configured": bool(self.settings.openai_api_key),
            "model": self.model,
            "init_error": self._init_error,
            "provider": "openai",
        }


def build_llm_client(settings: Settings) -> OpenAILLMClient:
    """工厂函数，便于未来替换为其他 LLM Provider。"""
    return OpenAILLMClient(settings)
