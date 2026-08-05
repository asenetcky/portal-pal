"""Settings for the data-prep job.

Same env file as the rest of portal-pal; only the keys this job needs are
declared, and `extra: ignore` lets the shared `.env` carry the others.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    app_env: str = "development"
    log_level: str = "INFO"
    max_retries: int = 3

    # Portal
    portal_domain: str = "data.ct.gov"

    # Supabase
    supabase_url: SecretStr
    supabase_key: SecretStr

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance - loaded once, reused everywhere."""

    return Settings()  # ty: ignore[missing-argument]
