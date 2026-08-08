"""Fixtures: a miniature catalog payload shaped like the real discovery API."""

from copy import deepcopy

import pytest


def _asset(
    asset_id: str,
    name: str,
    *,
    attribution: str = "CT Department of Public Health",
    columns: int = 2,
    tags: tuple[str, ...] = ("health", "ct"),
    parent: str | None = None,
) -> dict:
    return {
        "resource": {
            "name": name,
            "id": asset_id,
            "resource_name": None,
            "parent_fxf": [parent] if parent else [],
            "description": f"Description of {name}.",
            "attribution": attribution,
            "attribution_link": "https://portal.ct.gov/dph",
            "contact_email": "dph.data@ct.gov",
            "type": "dataset",
            "updatedAt": "2026-01-02T00:00:00.000Z",
            "createdAt": "2020-01-01T00:00:00.000Z",
            "metadata_updated_at": "2025-12-01T00:00:00.000Z",
            "data_updated_at": "2026-01-02T00:00:00.000Z",
            "page_views": {
                "page_views_last_week": 5,
                "page_views_last_month": 20,
                "page_views_total": 500,
                "page_views_last_week_log": 2.58,
                "page_views_last_month_log": 4.39,
                "page_views_total_log": 8.97,
            },
            "columns_name": [f"Column {i}" for i in range(columns)],
            "columns_field_name": [f"column_{i}" for i in range(columns)],
            "columns_datatype": ["Text"] * columns,
            "columns_description": [f"What column {i} holds" for i in range(columns)],
            "columns_format": [{"align": "left"} for _ in range(columns)],
            "download_count": 12,
            "provenance": "official",
            "lens_view_type": "tabular",
            "lens_display_type": "table",
            "locked": False,
            "backend": None,
            "blob_mime_type": None,
            "hide_from_data_json": False,
            "publication_date": "2020-01-02T00:00:00.000Z",
        },
        "classification": {
            "categories": [],
            "tags": [],
            "domain_category": "Health and Human Services",
            "domain_tags": list(tags),
            "domain_metadata": [
                {"key": "Agency_Agency", "value": "Department of Public Health"},
                {"key": "Details_Update-Frequency", "value": "Annual"},
            ],
        },
        "metadata": {
            "domain": "data.ct.gov",
            "license": "Public Domain",
            "access_points": {"text/html": f"https://data.ct.gov/d/{asset_id}"},
        },
        "permalink": f"https://data.ct.gov/d/{asset_id}",
        "link": f"https://data.ct.gov/dataset/{name.replace(' ', '-')}/{asset_id}",
        "owner": {"id": "aaaa-1111", "user_type": "interactive", "display_name": "Ada Lovelace"},
        "creator": {"id": "aaaa-1111", "user_type": "interactive", "display_name": "Ada Lovelace"},
    }


@pytest.fixture
def raw_record() -> dict:
    """A `raw_metadata` row with two assets."""
    return {
        "id": "snapshot-1",
        "domain": "data.ct.gov",
        "timestamp": "2026-01-03T00:00:00+00:00",
        "payload": [
            _asset("aaaa-0001", "Lead Screening"),
            _asset("bbbb-0002", "Birth Rates", attribution="Department of Public Health", parent="aaaa-0001"),
        ],
    }


@pytest.fixture
def next_record(raw_record: dict) -> dict:
    """The next scrape: one asset renamed, one retyped column, one tag gone,
    one asset added and one removed."""
    record = deepcopy(raw_record)
    record["id"] = "snapshot-2"
    record["timestamp"] = "2026-01-04T00:00:00+00:00"

    first = record["payload"][0]
    first["resource"]["name"] = "Lead Screening (Revised)"
    first["resource"]["columns_datatype"] = ["Number", "Text"]
    first["classification"]["domain_tags"] = ["health"]

    record["payload"].pop()  # bbbb-0002 removed
    record["payload"].append(_asset("cccc-0003", "Immunization Rates"))

    return record
