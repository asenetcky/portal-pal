"""Diffing two flattened snapshots."""

import polars as pl
from data_prep.changes import change_summary, diff_snapshots
from data_prep.flatten import flatten_record


def test_identical_snapshots_produce_no_changes(raw_record: dict) -> None:
    tables = flatten_record(raw_record)

    assert diff_snapshots(tables, tables).is_empty()


def test_added_and_removed_assets_are_reported(raw_record: dict, next_record: dict) -> None:
    changes = diff_snapshots(flatten_record(raw_record), flatten_record(next_record))

    assets = changes.filter(pl.col("entity") == "asset")

    assert set(assets.filter(pl.col("change_type") == "added")["asset_id"]) == {"cccc-0003"}
    assert set(assets.filter(pl.col("change_type") == "removed")["asset_id"]) == {"bbbb-0002"}


def test_renamed_asset_reports_the_field(raw_record: dict, next_record: dict) -> None:
    changes = diff_snapshots(flatten_record(raw_record), flatten_record(next_record))

    renamed = changes.filter((pl.col("change_type") == "modified") & (pl.col("field") == "name"))

    assert renamed.height == 1
    assert renamed["old_value"][0] == "Lead Screening"
    assert renamed["new_value"][0] == "Lead Screening (Revised)"


def test_retyped_column_is_reported(raw_record: dict, next_record: dict) -> None:
    changes = diff_snapshots(flatten_record(raw_record), flatten_record(next_record))

    retyped = changes.filter((pl.col("entity") == "column") & (pl.col("field") == "datatype"))

    assert retyped["entity_key"].to_list() == ["aaaa-0001|column_0"]
    assert retyped["old_value"].to_list() == ["Text"]
    assert retyped["new_value"].to_list() == ["Number"]


def test_dropped_tag_is_reported(raw_record: dict, next_record: dict) -> None:
    changes = diff_snapshots(flatten_record(raw_record), flatten_record(next_record))

    tags = changes.filter((pl.col("entity") == "tag") & (pl.col("change_type") == "removed"))

    assert "aaaa-0001|ct" in tags["entity_key"].to_list()


def test_changes_carry_both_snapshot_ids(raw_record: dict, next_record: dict) -> None:
    changes = diff_snapshots(flatten_record(raw_record), flatten_record(next_record))

    assert changes["from_snapshot_id"].unique().to_list() == ["snapshot-1"]
    assert changes["to_snapshot_id"].unique().to_list() == ["snapshot-2"]


def test_summary_counts_by_entity(raw_record: dict, next_record: dict) -> None:
    changes = diff_snapshots(flatten_record(raw_record), flatten_record(next_record))

    summary = change_summary(changes)

    assert summary["n"].sum() == changes.height
    assert set(summary["entity"]) <= {"asset", "column", "tag"}


def test_volatile_counters_are_not_tracked(raw_record: dict) -> None:
    """Page views move every scrape; they must not raise a change event."""
    noisier = {**raw_record, "id": "snapshot-2"}
    noisier["payload"] = [{**asset} for asset in raw_record["payload"]]
    noisier["payload"][0]["resource"] = {
        **raw_record["payload"][0]["resource"],
        "page_views": {**raw_record["payload"][0]["resource"]["page_views"], "page_views_total": 999_999},
    }

    changes = diff_snapshots(flatten_record(raw_record), flatten_record(noisier))

    assert changes.is_empty()
