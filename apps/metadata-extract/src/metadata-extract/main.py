import datetime as dt
from functools import lru_cache
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings
from sodapy import Socrata

from supabase import Client, create_client


class Settings(BaseSettings):
    # Application
    app_env: str = "development"
    log_level: str = "INFO"
    rate_limit: str = "20/minute"
    cache_ttl_seconds: int = 300
    max_cache_entries: int = 3
    max_retries: int = 3

    # portal
    portal_pal_api_key: SecretStr

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


class PortalMetaData(BaseModel):
    """Metadata on the Open Data Portal"""

    domain: str
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(default_factory=lambda: dt.datetime.now(tz=dt.UTC).isoformat())
    payload: list[dict[str, Any]]

    @classmethod
    def from_domain(cls, domain: str) -> "PortalMetaData":
        """
        Fetches the metadata from the domain and returns an initialized
        PortalMetaData instance.
        """
        settings = get_settings()

        with Socrata(domain, settings.portal_pal_api_key.get_secret_value()) as client:
            datasets = client.datasets()

            return cls(domain=domain, payload=datasets)


def get_client() -> Client:
    """
    Create the Supabase Client.
    """
    settings = get_settings()
    url = settings.supabase_url.get_secret_value()
    key = settings.supabase_key.get_secret_value()

    return create_client(url, key)


def upsert(metadata: PortalMetaData):
    """
    Upsert raw portal metadata into a staging table.
    """

    record = metadata.model_dump(mode="json")

    supabase = get_client()

    response = supabase.table("raw_metadata").upsert(record).execute()

    return response.data


def main():
    payload = PortalMetaData.from_domain("data.ct.gov")
    upsert(payload)


if __name__ == "__main__":
    main()
