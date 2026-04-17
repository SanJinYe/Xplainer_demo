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
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = defaults.DEFAULT_OPENROUTER_BASE_URL
    openrouter_model: str = defaults.DEFAULT_OPENROUTER_MODEL
    openrouter_site_url: Optional[str] = defaults.DEFAULT_OPENROUTER_SITE_URL
    openrouter_app_name: Optional[str] = defaults.DEFAULT_OPENROUTER_APP_NAME

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
    explanation_detailed_concurrency: int = (
        defaults.DEFAULT_EXPLANATION_DETAILED_CONCURRENCY
    )
    explanation_stream_flush_chars: int = defaults.DEFAULT_EXPLANATION_STREAM_FLUSH_CHARS
    explanation_stream_flush_ms: int = defaults.DEFAULT_EXPLANATION_STREAM_FLUSH_MS
    explanation_stream_stall_timeout_ms: int = (
        defaults.DEFAULT_EXPLANATION_STREAM_STALL_TIMEOUT_MS
    )

    summary_backend: Optional[str] = defaults.DEFAULT_SUMMARY_BACKEND
    summary_model: Optional[str] = defaults.DEFAULT_SUMMARY_MODEL
    summary_max_tokens: Optional[int] = defaults.DEFAULT_SUMMARY_MAX_TOKENS
    summary_timeout_ms: Optional[int] = defaults.DEFAULT_SUMMARY_TIMEOUT_MS
    detailed_backend: Optional[str] = defaults.DEFAULT_DETAILED_BACKEND
    detailed_model: Optional[str] = defaults.DEFAULT_DETAILED_MODEL
    detailed_max_tokens: Optional[int] = defaults.DEFAULT_DETAILED_MAX_TOKENS
    detailed_timeout_ms: Optional[int] = defaults.DEFAULT_DETAILED_TIMEOUT_MS

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TAILEVENTS_",
        env_ignore_empty=True,
        extra="ignore",
    )


__all__ = ["Settings"]
