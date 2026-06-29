import datetime as dt
from uuid import UUID, uuid4
from supabase import create_client, Client
from sodapy import Socrata
from backend.config import get_settings
from pydantic import BaseModel, Field
from typing import Any


class PortalMetaData(BaseModel):
    """Metadata on the Open Data Portal"""

    domain: str 
    id: UUID = Field(default_factory=uuid4)
    timestamp: str = Field(default_factory=lambda: dt.datetime.now(tz=dt.UTC).isoformat())
    payload: list[dict[str, Any]]

    @classmethod
    def from_domain(cls, domain:str) -> "PortalMetaData":
        """
        Fetches the metadata from the domain and returns an initialized
        PortalMetaData instance.
        """
        settings = get_settings()

        with Socrata(domain, settings.portal_pal_api_key.get_secret_value()) as client:
            datasets = client.datasets()

            return cls(
                domain = domain,
                payload = datasets
            )




def main():
    my_payload = PortalMetaData.from_domain("data.ct.gov")


if __name__ == "__main__":
    main()
