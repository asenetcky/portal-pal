"""The data-prep job: raw catalog JSON in Supabase -> tidy tables + RAG chunks.

    raw_metadata (one JSON blob per scrape)
        -> flatten        : eleven tidy frames
        -> replace        : portal_* tables for this snapshot
        -> diff           : portal_asset_changes vs the previous snapshot
        -> chunk + hash   : portal_chunks, new hashes left unembedded

Run it after `metadata-extract`:

    uv run python -m data_prep.main --domain data.ct.gov
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

import polars as pl
from supabase import Client

from data_prep import supabase_io as io
from data_prep.changes import change_summary, diff_snapshots
from data_prep.chunks import build_chunks, changed_chunks
from data_prep.config import get_settings
from data_prep.flatten import PortalTables, flatten_record

logger = logging.getLogger("data_prep")

# Frames needed to diff a snapshot; the rest are not read back out of the DB.
DIFF_TABLES = {
    "assets": "portal_assets",
    "asset_columns": "portal_asset_columns",
    "asset_tags": "portal_asset_tags",
}


def run(
    domain: str | None = None,
    snapshot_id: str | None = None,
    client: Client | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Flatten one raw snapshot into the portal tables. Returns a job report."""
    settings = get_settings()
    domain = domain or settings.portal_domain
    client = client or io.get_client()

    raw = io.fetch_raw_snapshot(client, domain, snapshot_id)
    tables = flatten_record(raw)
    current_id = raw["id"]

    logger.info("flattened snapshot %s (%s assets)", current_id, tables.assets.height)

    previous_id = io.previous_snapshot_id(client, domain, before=raw["timestamp"])
    previous = _load_previous(client, previous_id) if previous_id else None

    changes = diff_snapshots(previous, tables) if previous is not None else _no_changes()
    chunks = build_chunks(tables).unique(subset="content_hash", keep="first")
    known_hashes = set() if dry_run else io.known_chunk_hashes(client, tables.assets["asset_id"].to_list())
    fresh_chunks = changed_chunks(chunks, known_hashes)

    report: dict[str, Any] = {
        "snapshot_id": current_id,
        "domain": domain,
        "previous_snapshot_id": previous_id,
        "n_assets": tables.assets.height,
        "n_changes": changes.height,
        "n_chunks": chunks.height,
        "n_chunks_to_embed": fresh_chunks.height,
        "rows_written": {},
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info("dry run: %s", report)
        return report

    report["rows_written"] = _write_tables(client, current_id, tables)
    report["rows_written"][io.CHANGES_TABLE] = _write_changes(client, current_id, changes)
    report["rows_written"][io.CHUNKS_TABLE] = _write_chunks(client, current_id, chunks, fresh_chunks)

    logger.info("wrote %s", report["rows_written"])
    logger.info("changes:\n%s", change_summary(changes))

    return report


def _write_tables(client: Client, snapshot_id: str, tables: PortalTables) -> dict[str, int]:
    """Replace every snapshot-scoped table, so a re-run is idempotent."""
    written: dict[str, int] = {}

    for name, frame in tables.to_dict().items():
        table = io.TABLE_MAP[name]
        written[table] = io.replace_snapshot_rows(client, table, snapshot_id, frame)

    return written


def _write_changes(client: Client, snapshot_id: str, changes: pl.DataFrame) -> int:
    """Append this snapshot's change events, replacing any earlier attempt."""
    client.table(io.CHANGES_TABLE).delete().eq("to_snapshot_id", snapshot_id).execute()

    return io.insert_frame(client, io.CHANGES_TABLE, changes)


def _write_chunks(
    client: Client,
    snapshot_id: str,
    chunks: pl.DataFrame,
    fresh_chunks: pl.DataFrame,
) -> int:
    """Insert new chunk text; touch `last_seen_snapshot` on text we already have.

    Unchanged chunks keep their row -- and therefore their embedding -- which
    is the whole point of hashing the content.
    """
    payload = fresh_chunks.select(
        "content_hash",
        "asset_id",
        "chunk_index",
        "kind",
        "content",
        "n_chars",
        "metadata",
        first_seen_snapshot=pl.col("snapshot_id"),
        last_seen_snapshot=pl.col("snapshot_id"),
    )
    written = io.upsert_frame(client, io.CHUNKS_TABLE, payload, on_conflict="content_hash")

    seen_again = chunks.join(fresh_chunks.select("content_hash"), on="content_hash", how="anti")
    for batch in _hash_batches(seen_again["content_hash"].to_list()):
        client.table(io.CHUNKS_TABLE).update({"last_seen_snapshot": snapshot_id}).in_("content_hash", batch).execute()

    return written


def _hash_batches(hashes: list[str], size: int = io.FILTER_BATCH) -> list[list[str]]:
    return [hashes[start : start + size] for start in range(0, len(hashes), size)]


def _load_previous(client: Client, snapshot_id: str) -> PortalTables | None:
    """Rebuild just enough of the previous snapshot to diff against."""
    frames = {name: io.read_table(client, table, snapshot_id) for name, table in DIFF_TABLES.items()}

    if frames["assets"].is_empty():
        logger.warning("previous snapshot %s has no rows in portal_assets; skipping diff", snapshot_id)
        return None

    empty = pl.DataFrame()

    return PortalTables(
        snapshot=pl.DataFrame({"snapshot_id": [snapshot_id]}),
        assets=frames["assets"],
        people=empty,
        asset_people=empty,
        page_views=empty,
        asset_columns=frames["asset_columns"],
        asset_tags=frames["asset_tags"],
        domain_metadata=empty,
        access_points=empty,
        asset_parents=empty,
        org_signals=empty,
    )


def _no_changes() -> pl.DataFrame:
    from data_prep.changes import CHANGE_COLUMNS

    schema = {column: pl.String for column in (*CHANGE_COLUMNS, "from_snapshot_id", "to_snapshot_id")}

    return pl.DataFrame(schema=schema)


def main() -> None:
    parser = argparse.ArgumentParser(description="Flatten a raw portal snapshot into the portal_* tables.")
    parser.add_argument("--domain", default=None, help="portal domain (default: PORTAL_DOMAIN setting)")
    parser.add_argument("--snapshot-id", default=None, help="raw_metadata id (default: most recent scrape)")
    parser.add_argument("--dry-run", action="store_true", help="flatten and report, write nothing")
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    report = run(domain=args.domain, snapshot_id=args.snapshot_id, dry_run=args.dry_run)
    logger.info("done: %s", report)


if __name__ == "__main__":
    main()
