"""LLM client adapters for OpenAI-compatible chat providers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import Settings


class OpenAILLMClient:
    """OpenAI Chat Completions client wrapper."""

    provider = "openai"
    include_tool_choice = True

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = self._api_key()
        self.base_url = self._base_url()
        self.model = self._model()
        self.max_output_tokens = settings.openai_max_output_tokens
        self.enabled = bool(settings.openai_enabled and self.api_key)
        self._client: Optional[Any] = None
        self._init_error: Optional[str] = None

    @staticmethod
    def _clean(value: Optional[str]) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _api_key(self) -> Optional[str]:
        return self._clean(self.settings.openai_api_key)

    def _base_url(self) -> Optional[str]:
        return self._clean(self.settings.openai_base_url)

    def _model(self) -> str:
        return self._clean(self.settings.openai_model) or "gpt-4o-mini"

    def _get_client(self) -> Optional[Any]:
        """Create the SDK client lazily; return None when disabled or misconfigured."""
        if not self.enabled:
            return None
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            self._init_error = "The openai package is not installed. Run pip install -r requirements.txt."
            self.enabled = False
            return None

        kwargs: Dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": self.settings.openai_timeout_seconds,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        try:
            self._client = OpenAI(**kwargs)
        except Exception as exc:  # pragma: no cover - defensive SDK initialization guard
            self._init_error = str(exc)
            self.enabled = False
            return None
        return self._client

    def create_chat_completion(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Send one chat completion request through this provider adapter."""
        client = self._get_client()
        if client is None:
            raise RuntimeError(self._init_error or "LLM client is not configured.")

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_output_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            if self.include_tool_choice:
                kwargs["tool_choice"] = "auto"
        if response_format:
            kwargs["response_format"] = response_format
        return client.chat.completions.create(**kwargs)

    def status(self) -> Dict[str, Any]:
        """Return a safe runtime summary for health checks and audit events."""
        return {
            "enabled": self.enabled,
            "configured": bool(self.api_key),
            "model": self.model,
            "init_error": self._init_error,
            "provider": self.provider,
            "base_url": self.base_url,
        }


class SiliconFlowLLMClient(OpenAILLMClient):
    """SiliconFlow's OpenAI-compatible Chat Completions adapter."""

    provider = "siliconflow"
    include_tool_choice = False

    def _api_key(self) -> Optional[str]:
        return self._clean(self.settings.siliconflow_api_key) or self._clean(self.settings.openai_api_key)

    def _base_url(self) -> Optional[str]:
        return self._clean(self.settings.siliconflow_base_url) or "https://api.siliconflow.cn/v1"

    def _model(self) -> str:
        return self._clean(self.settings.siliconflow_model) or "deepseek-ai/DeepSeek-V4-Flash"


def build_llm_client(settings: Settings) -> OpenAILLMClient:
    """Factory function for the configured LLM provider."""
    provider = (settings.llm_provider or "openai").strip().lower()
    if provider in {"siliconflow", "sf"}:
        return SiliconFlowLLMClient(settings)
    return OpenAILLMClient(settings)
