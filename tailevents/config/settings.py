"""Application settings."""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

import tailevents.config.defaults as defaults


class Settings(BaseSettings):
    db_path: str = defaults.DEFAULT_DB_PATH

    llm_backend: str = defaults.DEFAULT_LLM_BACKEND
    ollama_base_url: str = defaults.DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = defaults.DEFAULT_OLLAMA_MODEL
    claude_api_key: Optional[str] = None
    claude_model: str = defaults.DEFAULT_CLAUDE_MODEL

    proxy_url: Optional[str] = defaults.DEFAULT_PROXY_URL
    no_proxy_hosts: list[str] = Field(
        default_factory=lambda: list(defaults.DEFAULT_NO_PROXY_HOSTS)
    )

    api_host: str = defaults.DEFAULT_API_HOST
    api_port: int = defaults.DEFAULT_API_PORT

    rename_similarity_threshold: float = defaults.DEFAULT_RENAME_SIMILARITY_THRESHOLD
    ast_parser: str = defaults.DEFAULT_AST_PARSER

    cache_enabled: bool = defaults.DEFAULT_CACHE_ENABLED
    cache_default_ttl: Optional[int] = defaults.DEFAULT_CACHE_DEFAULT_TTL

    explanation_max_events: int = defaults.DEFAULT_EXPLANATION_MAX_EVENTS
    explanation_temperature: float = defaults.DEFAULT_EXPLANATION_TEMPERATURE

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TAILEVENTS_",
        extra="ignore",
    )


__all__ = ["Settings"]
