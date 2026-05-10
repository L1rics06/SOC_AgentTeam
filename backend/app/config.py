from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # pragma: no cover - pydantic v1 compatibility
    from pydantic import BaseSettings  # type: ignore
    SettingsConfigDict = None  # type: ignore


class Settings(BaseSettings):
    app_name: str = "AI+SOC Agent Team PoC"
    api_prefix: str = "/api/v1"
    cors_origins: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    demo_mode: bool = True
    opensearch_url: Optional[str] = None
    opensearch_username: Optional[str] = None
    opensearch_password: Optional[str] = None
    opensearch_verify_certs: bool = True

    opensearch_alert_index: str = "wazuh-alerts-*"
    opensearch_findings_index: str = ".opensearch-sap-*"
    cases_index: str = "soc-cases-v1"
    traces_index: str = "soc-agent-traces-v1"
    knowledge_index: str = "soc-knowledge-v1"
    audit_index: str = "soc-audit-v1"

    if SettingsConfigDict is not None:  # pragma: no cover - exercised with pydantic v2
        model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    if SettingsConfigDict is None:  # pragma: no cover - pydantic v1 compatibility
        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
