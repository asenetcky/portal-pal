"""Reading raw snapshots from Supabase and writing the flattened tables back.

Two shapes of write:

* snapshot-scoped tables (`portal_assets`, `portal_asset_tags`, ...) are
  replaced wholesale for the snapshot being processed, so re-running the job
  on the same snapshot is idempotent;
* `portal_chunks` is keyed by content hash and upserted, so a chunk whose text
  did not change keeps the embedding it already has.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, cast

import polars as pl
import polars.selectors as cs
from supabase import Client, create_client

from data_prep.config import get_settings

RAW_TABLE = "raw_metadata"
CHUNKS_TABLE = "portal_chunks"
CHANGES_TABLE = "portal_asset_changes"
SNAPSHOTS_TABLE = "portal_snapshots"

# PostgREST caps a single response; page through anything bigger.
PAGE_SIZE = 1000
WRITE_BATCH = 500

# `in_(...)` filters ride in the URL query string, not the request body, so a
# batch of 64-char content hashes must stay well under the gateway's URL length
# limit (~8 KB). 50 hashes is ~3-4 KB of URL -- comfortably below it.
FILTER_BATCH = 50

# Flattened frame name -> destination table.
TABLE_MAP: dict[str, str] = {
    "snapshot": SNAPSHOTS_TABLE,
    "assets": "portal_assets",
    "people": "portal_people",
    "asset_people": "portal_asset_people",
    "page_views": "portal_page_views",
    "asset_columns": "portal_asset_columns",
    "asset_tags": "portal_asset_tags",
    "domain_metadata": "portal_domain_metadata",
    "access_points": "portal_access_points",
    "asset_parents": "portal_asset_parents",
    "org_signals": "portal_org_signals",
}

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S%.3fZ"


def get_client() -> Client:
    """Create the Supabase client from settings."""
    settings = get_settings()

    return create_client(settings.supabase_url.get_secret_value(), settings.supabase_key.get_secret_value())


def fetch_raw_snapshot(client: Client, domain: str, snapshot_id: str | None = None) -> dict[str, Any]:
    """Fetch one raw snapshot -- a named one, else the most recent scrape."""
    query = client.table(RAW_TABLE).select("*").eq("domain", domain)

    if snapshot_id is not None:
        query = query.eq("id", snapshot_id)
    else:
        query = query.order("timestamp", desc=True)

    rows = cast("list[dict[str, Any]]", query.limit(1).execute().data)

    if not rows:
        msg = f"no {RAW_TABLE} row for domain {domain!r}" + (f" and id {snapshot_id!r}" if snapshot_id else "")
        raise LookupError(msg)

    return rows[0]


def previous_snapshot_id(client: Client, domain: str, before: str) -> str | None:
    """The snapshot flattened most recently before `before` (an ISO string)."""
    response = (
        client.table(SNAPSHOTS_TABLE)
        .select("snapshot_id, scraped_at")
        .eq("domain", domain)
        .lt("scraped_at", before)
        .order("scraped_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = cast("list[dict[str, Any]]", response.data)

    return rows[0]["snapshot_id"] if rows else None


def read_table(client: Client, table: str, snapshot_id: str, columns: str = "*") -> pl.DataFrame:
    """Read every row of `table` for one snapshot, paging past the API cap."""
    rows: list[dict[str, Any]] = []

    for offset in _offsets():
        page = (
            client.table(table)
            .select(columns)
            .eq("snapshot_id", snapshot_id)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = cast("list[dict[str, Any]]", page.data)
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break

    if not rows:
        return pl.DataFrame()

    return pl.from_dicts(rows, infer_schema_length=None)


def replace_snapshot_rows(client: Client, table: str, snapshot_id: str, frame: pl.DataFrame) -> int:
    """Delete this snapshot's rows in `table`, then insert the new ones."""
    client.table(table).delete().eq("snapshot_id", snapshot_id).execute()

    return insert_frame(client, table, frame)


def insert_frame(client: Client, table: str, frame: pl.DataFrame) -> int:
    """Insert a frame in batches. Returns the number of rows sent."""
    written = 0

    for batch in _batches(frame):
        client.table(table).insert(batch).execute()
        written += len(batch)

    return written


def upsert_frame(client: Client, table: str, frame: pl.DataFrame, on_conflict: str) -> int:
    """Upsert a frame in batches, resolving collisions on `on_conflict`."""
    written = 0

    for batch in _batches(frame):
        client.table(table).upsert(batch, on_conflict=on_conflict).execute()
        written += len(batch)

    return written


def known_chunk_hashes(client: Client, asset_ids: Iterable[str] | None = None) -> set[str]:
    """Content hashes already in `portal_chunks`, so they are not re-embedded.

    Passing the snapshot's asset ids keeps the read small; omit it to pull the
    whole index.
    """
    hashes: set[str] = set()
    ids = list(asset_ids) if asset_ids is not None else None

    for offset in _offsets():
        query = client.table(CHUNKS_TABLE).select("content_hash")
        if ids is not None:
            query = query.in_("asset_id", ids)

        page = cast("list[dict[str, Any]]", query.range(offset, offset + PAGE_SIZE - 1).execute().data)
        hashes.update(str(row["content_hash"]) for row in page)
        if len(page) < PAGE_SIZE:
            break

    return hashes


def _offsets() -> Iterator[int]:
    offset = 0
    while True:
        yield offset
        offset += PAGE_SIZE


def _batches(frame: pl.DataFrame) -> Iterator[list[dict[str, Any]]]:
    """Yield JSON-ready record batches; datetimes become ISO-8601 text."""
    if frame.is_empty():
        return

    prepared = frame.with_columns(cs.datetime().dt.convert_time_zone("UTC").dt.strftime(_TIMESTAMP_FORMAT))

    records = prepared.to_dicts()
    for start in range(0, len(records), WRITE_BATCH):
        yield records[start : start + WRITE_BATCH]
