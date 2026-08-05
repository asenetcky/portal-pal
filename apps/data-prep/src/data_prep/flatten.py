"""Flatten a raw Socrata catalog payload into tidy polars frames.

The `raw_metadata` rows written by `metadata-extract` hold the whole catalog
response as one JSON blob: deeply nested, and mixing several entities into a
single record. This module splits that blob along its logical seams so each
frame answers one kind of question -- and so a diff between two snapshots can
point at a field instead of a blob.

    snapshot         one row per scrape
    assets           one row per asset (dataset, chart, story, ...)
    people           one row per distinct portal user
    asset_people     asset <-> person bridge, with role (owner / creator)
    page_views       traffic counters, kept out of the wide asset row
    asset_columns    one row per column of a tabular asset
    asset_tags       one row per domain tag
    domain_metadata  one row per custom key/value pair
    access_points    one row per external link / download format
    asset_parents    one row per parent asset (derived views, maps, ...)
    org_signals      long table of every field that hints at ownership

Portal agnostic: nothing here knows about a particular domain or agency.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import polars as pl

# Top-level keys of one catalog record, as returned by the discovery API.
PAYLOAD_KEYS: tuple[str, ...] = (
    "resource",
    "classification",
    "metadata",
    "permalink",
    "link",
    "owner",
    "creator",
)

_TIMESTAMP_COLUMNS: tuple[str, ...] = (
    "updated_at",
    "created_at",
    "metadata_updated_at",
    "data_updated_at",
    "publication_date",
)


@dataclass(frozen=True)
class PortalTables:
    """The flattened frames for one snapshot."""

    snapshot: pl.DataFrame
    assets: pl.DataFrame
    people: pl.DataFrame
    asset_people: pl.DataFrame
    page_views: pl.DataFrame
    asset_columns: pl.DataFrame
    asset_tags: pl.DataFrame
    domain_metadata: pl.DataFrame
    access_points: pl.DataFrame
    asset_parents: pl.DataFrame
    org_signals: pl.DataFrame

    def to_dict(self) -> dict[str, pl.DataFrame]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def write_parquet(self, directory: str | Path) -> None:
        """Write every frame to `directory/<name>.parquet` (local debugging)."""
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        for name, frame in self.to_dict().items():
            frame.write_parquet(out / f"{name}.parquet")

    def summary(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "table": list(self.to_dict()),
                "rows": [f.height for f in self.to_dict().values()],
                "columns": [f.width for f in self.to_dict().values()],
            }
        )


def flatten_record(record: Mapping[str, Any]) -> PortalTables:
    """Flatten one `raw_metadata` row.

    Expects the shape written by `metadata-extract`: `id`, `domain`,
    `timestamp`, and `payload` (the list of catalog records). `payload` may
    arrive as a JSON string if the driver did not decode the jsonb column.
    """
    payload = record["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    if not payload:
        msg = f"snapshot {record.get('id')!r} has an empty payload"
        raise ValueError(msg)

    # infer_schema_length=None: nested keys (access point labels, column
    # formats) appear well past the default 100-row sample.
    records = pl.from_dicts(list(payload), strict=False, infer_schema_length=None)

    # A portal that omits a whole section (no `creator`, no `metadata`) still
    # has to produce frames of the same shape, so missing keys become nulls.
    missing = [pl.lit(None, dtype=pl.String).alias(key) for key in PAYLOAD_KEYS if key not in records.columns]

    base = (
        records.with_columns(missing)
        .select(PAYLOAD_KEYS)
        .with_columns(
            snapshot_id=pl.lit(record["id"]),
            snapshot_timestamp=pl.lit(record["timestamp"]),
            asset_id=pl.col("resource").struct.field("id"),
        )
    )

    return PortalTables(
        snapshot=_snapshot(record, base),
        assets=_assets(base),
        people=_people(base),
        asset_people=_asset_people(base),
        page_views=_page_views(base),
        asset_columns=_asset_columns(base),
        asset_tags=_asset_tags(base),
        domain_metadata=_domain_metadata(base),
        access_points=_access_points(base),
        asset_parents=_asset_parents(base),
        org_signals=_org_signals(base),
    )


def flatten_file(path: str | Path) -> PortalTables:
    """Flatten a raw snapshot saved to disk. Same JSON shape as the table."""
    return flatten_record(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------


def _keys() -> list[pl.Expr]:
    """Columns carried onto every child frame."""
    return [pl.col("snapshot_id"), pl.col("asset_id")]


def _struct_fields(base: pl.DataFrame, column: str) -> set[str]:
    dtype = base.schema[column]

    return set(dtype.to_schema()) if isinstance(dtype, pl.Struct) else set()


def _getter(base: pl.DataFrame, column: str):
    """Field accessor that tolerates keys the portal did not send.

    Optional keys (`access_points`, `license`, `blob_mime_type`, ...) are
    absent from the inferred struct on some domains and some scrapes; asking
    for them directly raises. This returns a typed null instead, so the frame
    keeps its shape across portals.
    """
    available = _struct_fields(base, column)

    def field(name: str, dtype: pl.DataType | type[pl.DataType] = pl.String) -> pl.Expr:
        if name in available:
            return pl.col(column).struct.field(name)
        return pl.lit(None, dtype=dtype)

    return field


def _to_datetime(name: str) -> pl.Expr:
    return pl.col(name).str.to_datetime(strict=False, time_zone="UTC")


def _snapshot(record: Mapping[str, Any], base: pl.DataFrame) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "snapshot_id": [record["id"]],
            "domain": [record["domain"]],
            "n_assets": [base.height],
        }
    ).with_columns(scraped_at=pl.lit(record["timestamp"]).str.to_datetime(strict=False, time_zone="UTC"))


def _assets(base: pl.DataFrame) -> pl.DataFrame:
    """The wide, one-row-per-asset frame -- scalars only, no nesting."""
    resource = _getter(base, "resource")
    metadata = _getter(base, "metadata")
    classification = _getter(base, "classification")
    owner = _getter(base, "owner")
    creator = _getter(base, "creator")
    string_list = pl.List(pl.String)

    assets = base.select(
        *_keys(),
        metadata("domain").alias("domain"),
        resource("name").alias("name"),
        resource("type").alias("type"),
        resource("description").alias("description"),
        resource("attribution").alias("attribution"),
        resource("attribution_link").alias("attribution_link"),
        resource("contact_email").alias("contact_email"),
        classification("domain_category").alias("domain_category"),
        metadata("license").alias("license"),
        resource("provenance").alias("provenance"),
        resource("backend").alias("backend"),
        resource("lens_view_type").alias("lens_view_type"),
        resource("lens_display_type").alias("lens_display_type"),
        resource("blob_mime_type").alias("blob_mime_type"),
        resource("resource_name").alias("resource_name"),
        resource("locked", pl.Boolean).alias("locked"),
        resource("hide_from_data_json", pl.Boolean).alias("hide_from_data_json"),
        resource("download_count", pl.Int64).alias("download_count"),
        resource("updatedAt").alias("updated_at"),
        resource("createdAt").alias("created_at"),
        resource("metadata_updated_at").alias("metadata_updated_at"),
        resource("data_updated_at").alias("data_updated_at"),
        resource("publication_date").alias("publication_date"),
        pl.col("permalink"),
        pl.col("link"),
        owner("id").alias("owner_id"),
        owner("display_name").alias("owner_name"),
        creator("id").alias("creator_id"),
        creator("display_name").alias("creator_name"),
        resource("columns_name", string_list).list.len().alias("n_columns"),
        resource("parent_fxf", string_list).list.len().alias("n_parents"),
        classification("domain_tags", string_list).list.len().alias("n_tags"),
    )

    return assets.with_columns([_to_datetime(column) for column in _TIMESTAMP_COLUMNS])


def _people(base: pl.DataFrame) -> pl.DataFrame:
    """Distinct portal users, with how often they own / create assets."""
    roles = _asset_people(base)

    counts = (
        roles.group_by("person_id", "role")
        .len("n")
        .pivot(on="role", index="person_id", values="n")
        .fill_null(0)
        .rename(lambda column: {"owner": "n_owned", "creator": "n_created"}.get(column, column))
    )

    people = (
        roles.select("snapshot_id", "person_id", "display_name", "user_type")
        .unique(subset="person_id", keep="first")
        .join(counts, on="person_id", how="left")
    )

    return people.sort("display_name")


def _asset_people(base: pl.DataFrame) -> pl.DataFrame:
    """Long asset <-> person bridge, one row per role held on an asset."""
    frames = []
    for role in ("owner", "creator"):
        person = _getter(base, role)
        frames.append(
            base.select(
                *_keys(),
                pl.lit(role).alias("role"),
                person("id").alias("person_id"),
                person("display_name").alias("display_name"),
                person("user_type").alias("user_type"),
            )
        )

    return pl.concat(frames).filter(pl.col("person_id").is_not_null())


def _page_views(base: pl.DataFrame) -> pl.DataFrame:
    if "page_views" not in _struct_fields(base, "resource"):
        return base.select(*_keys())

    return base.select(*_keys(), pl.col("resource").struct.field("page_views").struct.unnest())


def _asset_columns(base: pl.DataFrame) -> pl.DataFrame:
    """One row per column of a tabular asset.

    The API returns column metadata as five parallel arrays; they are zipped
    back together positionally.
    """
    resource = _getter(base, "resource")
    string_list = pl.List(pl.String)
    has_format = "columns_format" in _struct_fields(base, "resource")

    exploded = (
        base.select(
            *_keys(),
            pl.int_ranges(resource("columns_name", string_list).list.len()).alias("position"),
            resource("columns_name", string_list).alias("column_name"),
            resource("columns_field_name", string_list).alias("field_name"),
            resource("columns_datatype", string_list).alias("datatype"),
            resource("columns_description", string_list).alias("description"),
            resource("columns_format", string_list).alias("format"),
        )
        .filter(pl.col("column_name").list.len() > 0)
        .explode("position", "column_name", "field_name", "datatype", "description", "format")
    )

    # Display format is a grab bag of 17 rarely-used keys; it stays JSON
    # rather than becoming 17 mostly-null columns.
    encoded = pl.col("format").struct.json_encode() if has_format else pl.lit(None, dtype=pl.String)

    return exploded.with_columns(encoded.alias("format"))


def _asset_tags(base: pl.DataFrame) -> pl.DataFrame:
    classification = _getter(base, "classification")

    return (
        base.select(*_keys(), classification("domain_tags", pl.List(pl.String)).alias("tag"))
        .explode("tag")
        .filter(pl.col("tag").is_not_null())
    )


def _domain_metadata(base: pl.DataFrame) -> pl.DataFrame:
    """Custom key/value metadata, with the `Group_Field` key split out."""
    pair_dtype = pl.List(pl.Struct({"key": pl.String, "value": pl.String}))

    if "domain_metadata" not in _struct_fields(base, "classification"):
        return (
            base.select(*_keys())
            .clear()
            .with_columns(
                key=pl.lit(None, dtype=pl.String),
                value=pl.lit(None, dtype=pl.String),
                metadata_group=pl.lit(None, dtype=pl.String),
                metadata_field=pl.lit(None, dtype=pl.String),
            )
        )

    return (
        base.select(*_keys(), _getter(base, "classification")("domain_metadata", pair_dtype).alias("pair"))
        .explode("pair")
        .filter(pl.col("pair").is_not_null())
        .with_columns(pl.col("pair").struct.unnest())
        .drop("pair")
        .with_columns(
            # `group` and `field` are awkward column names in SQL, so the
            # split halves of `Group_Field` keys get spelled out.
            metadata_group=pl.col("key").str.split("_").list.first(),
            metadata_field=pl.col("key").str.splitn("_", 2).struct.field("field_1"),
        )
    )


def _access_points(base: pl.DataFrame) -> pl.DataFrame:
    """External links, both the flat `access_points` map and the rich list.

    The API keys these by format or by a free-text label, so the struct is
    unpivoted back to (label, url) rows instead of staying a wide schema that
    grows a column every time somebody invents a new label.
    """
    metadata = pl.col("metadata").struct
    available = _struct_fields(base, "metadata")
    columns = ["snapshot_id", "asset_id", "source", "label", "url", "title", "description"]

    frames = [_empty_access_points(base, columns)]

    if "access_points" in available:
        frames.append(
            _unpivot_urls(base.select(*_keys(), metadata.field("access_points").struct.unnest()))
            .with_columns(
                source=pl.lit("access_points"),
                title=pl.lit(None, dtype=pl.String),
                description=pl.lit(None, dtype=pl.String),
            )
            .select(columns)
        )

    if "additional_access_points" in available:
        rich = (
            base.select(*_keys(), metadata.field("additional_access_points").alias("point"))
            .explode("point")
            .filter(pl.col("point").is_not_null())
            .with_columns(pl.col("point").struct.unnest())
            .drop("point")
        )
        frames.append(
            rich.select("snapshot_id", "asset_id", "title", "description", pl.col("urls").struct.unnest())
            .pipe(_unpivot_urls, index=["snapshot_id", "asset_id", "title", "description"])
            .with_columns(source=pl.lit("additional_access_points"))
            .select(columns)
        )

    return pl.concat(frames)


def _empty_access_points(base: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    """Typed empty frame, so a portal with no access points still concats."""
    return (
        base.select(*_keys())
        .clear()
        .with_columns([pl.lit(None, dtype=pl.String).alias(column) for column in columns[2:]])
        .select(columns)
    )


def _unpivot_urls(frame: pl.DataFrame, index: list[str] | None = None) -> pl.DataFrame:
    """Turn a wide `{label: url}` struct-unnest into long (label, url) rows."""
    index = index or ["snapshot_id", "asset_id"]
    on = [column for column in frame.columns if column not in index]

    if not on:
        return frame.select(
            *index,
            pl.lit(None, dtype=pl.String).alias("label"),
            pl.lit(None, dtype=pl.String).alias("url"),
        ).clear()

    return frame.unpivot(index=index, on=on, variable_name="label", value_name="url").filter(
        pl.col("url").is_not_null()
    )


def _asset_parents(base: pl.DataFrame) -> pl.DataFrame:
    """Derived assets (charts, filters, maps) linked to their parent dataset."""
    return (
        base.select(*_keys(), _getter(base, "resource")("parent_fxf", pl.List(pl.String)).alias("parent_id"))
        .explode("parent_id")
        .filter(pl.col("parent_id").is_not_null())
    )


def _org_signals(base: pl.DataFrame) -> pl.DataFrame:
    """Every field that hints at which org an asset belongs to, long form.

    Portals label ownership inconsistently -- attribution string, custom
    `Agency_*` metadata, contact email, tags, the publishing user. Rather than
    pick one, collect them all as (source, key, value) rows so a downstream
    step can decide which sources it trusts.
    """
    resource = _getter(base, "resource")
    classification = _getter(base, "classification")

    scalar_sources = {
        "attribution": resource("attribution"),
        "attribution_link": resource("attribution_link"),
        "contact_email": resource("contact_email"),
        "domain_category": classification("domain_category"),
        "owner": _getter(base, "owner")("display_name"),
        "creator": _getter(base, "creator")("display_name"),
        "name": resource("name"),
        "description": resource("description"),
        "link": pl.col("link"),
    }

    frames = [
        base.select(*_keys(), pl.lit(source).alias("source"), pl.lit(source).alias("key"), expr.alias("value"))
        for source, expr in scalar_sources.items()
    ]

    frames.append(
        _asset_tags(base).select(
            "snapshot_id",
            "asset_id",
            pl.lit("domain_tag").alias("source"),
            pl.lit("domain_tag").alias("key"),
            pl.col("tag").alias("value"),
        )
    )

    frames.append(
        _domain_metadata(base).select(
            "snapshot_id",
            "asset_id",
            pl.lit("domain_metadata").alias("source"),
            pl.col("key"),
            pl.col("value"),
        )
    )

    return pl.concat(frames).filter(pl.col("value").is_not_null() & (pl.col("value") != "")).sort("asset_id", "source")
