"""Turn flattened snapshot frames into RAG chunks.

One asset becomes a short overview document plus, if it is tabular, one or
more schema documents listing its columns. Each chunk carries a content hash
so the embedding step only re-embeds what actually changed between snapshots
-- the whole reason the catalog gets flattened before it gets indexed.

Chunk text is deliberately plain markdown: it reads well to a human in a
citation and tokenizes cheaply.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable

import polars as pl

from data_prep.flatten import PortalTables

# Columns per schema chunk. Enough context to answer "does this dataset have
# a town field", small enough to stay well inside an embedding window.
COLUMNS_PER_CHUNK = 30

CHUNK_COLUMNS: tuple[str, ...] = (
    "snapshot_id",
    "asset_id",
    "chunk_index",
    "kind",
    "content",
    "content_hash",
    "n_chars",
    "metadata",
)


def build_chunks(tables: PortalTables, columns_per_chunk: int = COLUMNS_PER_CHUNK) -> pl.DataFrame:
    """Build every chunk for one snapshot, one row per chunk."""
    context = _asset_context(tables)

    rows: list[dict[str, object]] = []
    for asset in context.iter_rows(named=True):
        for index, (kind, content) in enumerate(_documents(asset, columns_per_chunk)):
            rows.append(
                {
                    "snapshot_id": asset["snapshot_id"],
                    "asset_id": asset["asset_id"],
                    "chunk_index": index,
                    "kind": kind,
                    "content": content,
                    "content_hash": content_hash(content),
                    "n_chars": len(content),
                    "metadata": json.dumps(_chunk_metadata(asset, kind)),
                }
            )

    if not rows:
        return pl.DataFrame(schema={column: pl.String for column in CHUNK_COLUMNS})

    return pl.DataFrame(rows).select(CHUNK_COLUMNS)


def content_hash(content: str) -> str:
    """Stable hash of chunk text -- the re-embedding trigger."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def changed_chunks(chunks: pl.DataFrame, known_hashes: Iterable[str]) -> pl.DataFrame:
    """Chunks whose text is new to the index and therefore needs embedding."""
    known = set(known_hashes)
    if not known:
        return chunks

    return chunks.filter(~pl.col("content_hash").is_in(list(known)))


def _asset_context(tables: PortalTables) -> pl.DataFrame:
    """One row per asset carrying its tags, custom metadata, links, columns."""
    tags = tables.asset_tags.group_by("asset_id").agg(tags=pl.col("tag").unique().sort())

    metadata = (
        tables.domain_metadata.filter(pl.col("value").is_not_null())
        .group_by("asset_id")
        .agg(metadata_pairs=pl.concat_str([pl.col("key"), pl.col("value")], separator=": "))
    )

    links = tables.access_points.group_by("asset_id").agg(
        links=pl.concat_str([pl.col("label").fill_null("link"), pl.col("url")], separator=": ")
    )

    columns = (
        tables.asset_columns.sort("position")
        .group_by("asset_id")
        .agg(
            column_lines=pl.concat_str(
                [
                    pl.col("column_name").fill_null(pl.col("field_name")),
                    pl.col("datatype").fill_null("unknown"),
                    pl.col("description").fill_null(""),
                ],
                separator=" | ",
            )
        )
    )

    return (
        tables.assets.join(tags, on="asset_id", how="left")
        .join(metadata, on="asset_id", how="left")
        .join(links, on="asset_id", how="left")
        .join(columns, on="asset_id", how="left")
    )


def _documents(asset: dict, columns_per_chunk: int) -> list[tuple[str, str]]:
    documents = [("overview", _overview(asset))]

    lines = asset.get("column_lines") or []
    for start in range(0, len(lines), columns_per_chunk):
        window = lines[start : start + columns_per_chunk]
        part = start // columns_per_chunk + 1
        documents.append(("schema", _schema(asset, window, part, start)))

    return documents


def _overview(asset: dict) -> str:
    parts = [
        f"# {asset['name']}",
        "",
        f"Asset type: {asset['type']}",
        f"Portal: {asset['domain']}",
        f"Publisher: {asset['attribution'] or 'unstated'}",
        f"Owner: {asset['owner_name'] or 'unknown'}",
        f"Category: {asset['domain_category'] or 'uncategorized'}",
    ]

    if asset.get("tags"):
        parts.append(f"Tags: {', '.join(asset['tags'])}")
    if asset.get("contact_email"):
        parts.append(f"Contact: {asset['contact_email']}")
    if asset.get("license"):
        parts.append(f"License: {asset['license']}")

    parts += [
        f"Data last updated: {_date(asset.get('data_updated_at'))}",
        f"Metadata last updated: {_date(asset.get('metadata_updated_at'))}",
        f"Created: {_date(asset.get('created_at'))}",
        f"Columns: {asset.get('n_columns') or 0}",
        f"Link: {asset['permalink'] or asset['link'] or ''}",
    ]

    if asset.get("description"):
        parts += ["", "## Description", asset["description"]]

    if asset.get("metadata_pairs"):
        parts += ["", "## Portal metadata", *[f"- {pair}" for pair in asset["metadata_pairs"]]]

    if asset.get("links"):
        parts += ["", "## Access points", *[f"- {link}" for link in asset["links"]]]

    return "\n".join(parts)


def _schema(asset: dict, lines: list[str], part: int, start: int) -> str:
    header = [
        f"# {asset['name']} — column reference (part {part})",
        "",
        f"Asset type: {asset['type']}. Publisher: {asset['attribution'] or 'unstated'}.",
        f"Columns {start + 1}-{start + len(lines)} of {asset.get('n_columns') or len(lines)}.",
        "",
        "name | datatype | description",
    ]

    return "\n".join([*header, *[f"- {line}" for line in lines]])


def _chunk_metadata(asset: dict, kind: str) -> dict:
    """Filterable metadata to store alongside the vector."""
    return {
        "kind": kind,
        "asset_id": asset["asset_id"],
        "name": asset["name"],
        "type": asset["type"],
        "domain": asset["domain"],
        "attribution": asset["attribution"],
        "domain_category": asset["domain_category"],
        "owner_name": asset["owner_name"],
        "permalink": asset["permalink"],
        "data_updated_at": _date(asset.get("data_updated_at")),
        "tags": asset.get("tags") or [],
    }


def _date(value: object) -> str:
    """Dates read better than timestamps in a retrieved chunk."""
    if value is None:
        return "unknown"
    if isinstance(value, dt.datetime | dt.date):
        return value.date().isoformat() if isinstance(value, dt.datetime) else value.isoformat()
    return str(value)
