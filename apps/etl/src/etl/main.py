import datetime as dt
from typing import Any
from uuid import uuid4

from backend.config import get_settings
from pydantic import BaseModel, Field
from sodapy import Socrata
from supabase import Client, create_client


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
