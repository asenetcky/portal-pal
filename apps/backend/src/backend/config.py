"""
Centralized Configuration
Uses pydantic-settings for vlaidated environment variables.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM Configuration
    openai_api_key: SecretStr
    primary_model: str = "gpt-5.4-nano-2026-03-17"
    fallback_model: str = "gpt-5-nano-2025-08-07"

    # LangSmith
    langsmith_tracing: bool = True
    langsmith_api_key: str = ""
    langsmith_project: str = ""
    langsmith_endpoint: str = ""

    # Application
    app_env: str = "development"
    log_level: str = "INFO"
    rate_limit: str = "20/minute"
    cache_ttl_seconds: int = 300
    max_cache_entries: int = 3
    max_retries: int = 3

    # Auth
    portal_pal_api_key: SecretStr

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance - loaded once, reused everywhere."""

    return Settings()  # ty: ignore[missing-argument]
