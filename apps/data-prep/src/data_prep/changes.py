"""Diff two flattened snapshots into field-level change events.

Monitoring the portal means answering "what moved since yesterday" without
re-reading two 11 MB blobs. Once both snapshots are flattened, that question
is a join: which assets appeared, which vanished, and which fields changed
value on the ones that stayed.

Volatile counters (page views, downloads) are deliberately not tracked --
they change on every scrape and would drown the signal.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from data_prep.flatten import PortalTables

# Asset fields worth an alert. Descriptive and structural only.
TRACKED_ASSET_FIELDS: tuple[str, ...] = (
    "name",
    "type",
    "description",
    "attribution",
    "attribution_link",
    "contact_email",
    "domain_category",
    "license",
    "provenance",
    "backend",
    "lens_view_type",
    "blob_mime_type",
    "locked",
    "hide_from_data_json",
    "owner_id",
    "owner_name",
    "creator_id",
    "creator_name",
    "permalink",
    "link",
    "data_updated_at",
    "metadata_updated_at",
    "publication_date",
    "n_columns",
    "n_tags",
)

# Column fields worth an alert -- a renamed or retyped column breaks pipelines.
TRACKED_COLUMN_FIELDS: tuple[str, ...] = (
    "column_name",
    "datatype",
    "description",
    "position",
)

CHANGE_COLUMNS: tuple[str, ...] = (
    "entity",
    "entity_key",
    "asset_id",
    "change_type",
    "field",
    "old_value",
    "new_value",
)


def diff_snapshots(previous: PortalTables, current: PortalTables) -> pl.DataFrame:
    """All change events between two flattened snapshots.

    Returns one row per (entity, change, field) with `from_snapshot_id` and
    `to_snapshot_id` attached, ready to append to `portal_asset_changes`.
    """
    changes = pl.concat(
        [
            diff_frames(
                previous.assets,
                current.assets,
                key=["asset_id"],
                tracked=TRACKED_ASSET_FIELDS,
                entity="asset",
            ),
            diff_frames(
                previous.asset_columns,
                current.asset_columns,
                key=["asset_id", "field_name"],
                tracked=TRACKED_COLUMN_FIELDS,
                entity="column",
            ),
            diff_membership(
                previous.asset_tags,
                current.asset_tags,
                key=["asset_id", "tag"],
                entity="tag",
            ),
        ]
    )

    from_id = _snapshot_id(previous)
    to_id = _snapshot_id(current)

    return changes.with_columns(
        from_snapshot_id=pl.lit(from_id),
        to_snapshot_id=pl.lit(to_id),
    ).sort("entity", "entity_key", "field")


def diff_frames(
    previous: pl.DataFrame,
    current: pl.DataFrame,
    key: Sequence[str],
    tracked: Sequence[str],
    entity: str,
) -> pl.DataFrame:
    """Added / removed / modified rows between two versions of one frame.

    `key` identifies a row across snapshots; `tracked` are the fields compared
    value by value. Everything is cast to text first so one long frame can
    carry changes from columns of any dtype.
    """
    key = list(key)
    tracked = [field for field in tracked if field in previous.columns and field in current.columns]

    before = _stringify(previous, key, tracked)
    after = _stringify(current, key, tracked)

    added = after.join(before, on=key, how="anti").with_columns(
        change_type=pl.lit("added"),
        field=pl.lit(None, dtype=pl.String),
        old_value=pl.lit(None, dtype=pl.String),
        new_value=pl.lit(None, dtype=pl.String),
    )
    removed = before.join(after, on=key, how="anti").with_columns(
        change_type=pl.lit("removed"),
        field=pl.lit(None, dtype=pl.String),
        old_value=pl.lit(None, dtype=pl.String),
        new_value=pl.lit(None, dtype=pl.String),
    )

    if tracked:
        before_long = before.unpivot(index=key, on=tracked, variable_name="field", value_name="old_value")
        after_long = after.unpivot(index=key, on=tracked, variable_name="field", value_name="new_value")
        modified = (
            before_long.join(after_long, on=[*key, "field"], how="inner")
            .filter(pl.col("old_value").ne_missing(pl.col("new_value")))
            .with_columns(change_type=pl.lit("modified"))
        )
    else:
        modified = added.clear()

    return pl.concat([added, removed, modified], how="diagonal").pipe(_shape, key=key, entity=entity)


def diff_membership(
    previous: pl.DataFrame,
    current: pl.DataFrame,
    key: Sequence[str],
    entity: str,
) -> pl.DataFrame:
    """Set difference for frames that are membership only (tags, parents)."""
    key = list(key)
    before = previous.select(key).unique()
    after = current.select(key).unique()

    added = after.join(before, on=key, how="anti").with_columns(change_type=pl.lit("added"))
    removed = before.join(after, on=key, how="anti").with_columns(change_type=pl.lit("removed"))

    return (
        pl.concat([added, removed])
        .with_columns(
            field=pl.lit(None, dtype=pl.String),
            old_value=pl.lit(None, dtype=pl.String),
            new_value=pl.lit(None, dtype=pl.String),
        )
        .pipe(_shape, key=key, entity=entity)
    )


def change_summary(changes: pl.DataFrame) -> pl.DataFrame:
    """Counts by entity and change type -- the line for a digest email."""
    if changes.is_empty():
        return pl.DataFrame(schema={"entity": pl.String, "change_type": pl.String, "n": pl.UInt32})

    return changes.group_by("entity", "change_type").len("n").sort("entity", "change_type")


def _stringify(frame: pl.DataFrame, key: Sequence[str], tracked: Sequence[str]) -> pl.DataFrame:
    return frame.select(*key, *[pl.col(field).cast(pl.String) for field in tracked])


def _shape(frame: pl.DataFrame, key: Sequence[str], entity: str) -> pl.DataFrame:
    """Collapse the key columns into one printable `entity_key`."""
    entity_key = pl.concat_str([pl.col(column).cast(pl.String).fill_null("") for column in key], separator="|")
    asset_id = pl.col("asset_id") if "asset_id" in key else pl.lit(None, dtype=pl.String)

    return frame.select(
        pl.lit(entity).alias("entity"),
        entity_key.alias("entity_key"),
        asset_id.alias("asset_id"),
        pl.col("change_type"),
        pl.col("field"),
        pl.col("old_value"),
        pl.col("new_value"),
    )


def _snapshot_id(tables: PortalTables) -> str | None:
    if tables.snapshot.is_empty():
        return None
    return tables.snapshot["snapshot_id"][0]
