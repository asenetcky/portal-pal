"""Flattening one raw snapshot into tidy frames."""

import json

import polars as pl
import pytest
from data_prep.flatten import flatten_record


def test_every_frame_carries_the_snapshot_id(raw_record: dict) -> None:
    tables = flatten_record(raw_record)

    for name, frame in tables.to_dict().items():
        assert "snapshot_id" in frame.columns, name
        assert frame["snapshot_id"].unique().to_list() == ["snapshot-1"], name


def test_assets_are_one_row_per_asset(raw_record: dict) -> None:
    assets = flatten_record(raw_record).assets

    assert assets.height == 2
    assert assets["asset_id"].to_list() == ["aaaa-0001", "bbbb-0002"]
    assert assets["name"].to_list() == ["Lead Screening", "Birth Rates"]
    assert assets["data_updated_at"].dtype == pl.Datetime(time_unit="us", time_zone="UTC")


def test_columns_zip_the_parallel_arrays(raw_record: dict) -> None:
    columns = flatten_record(raw_record).asset_columns

    first = columns.filter(pl.col("asset_id") == "aaaa-0001").sort("position")

    assert first["column_name"].to_list() == ["Column 0", "Column 1"]
    assert first["field_name"].to_list() == ["column_0", "column_1"]
    assert json.loads(first["format"][0]) == {"align": "left"}


def test_people_are_deduplicated_with_role_counts(raw_record: dict) -> None:
    tables = flatten_record(raw_record)

    assert tables.people.height == 1
    assert tables.people["n_owned"].to_list() == [2]
    assert tables.asset_people.height == 4  # two assets x owner + creator


def test_domain_metadata_splits_the_key(raw_record: dict) -> None:
    metadata = flatten_record(raw_record).domain_metadata

    agency = metadata.filter(pl.col("key") == "Agency_Agency")

    assert agency["metadata_group"].to_list() == ["Agency", "Agency"]
    assert agency["metadata_field"].to_list() == ["Agency", "Agency"]
    assert agency["value"].unique().to_list() == ["Department of Public Health"]


def test_access_points_become_label_url_rows(raw_record: dict) -> None:
    points = flatten_record(raw_record).access_points

    assert points["label"].unique().to_list() == ["text/html"]
    assert points["url"][0].startswith("https://data.ct.gov/d/")


def test_org_signals_collect_every_ownership_hint(raw_record: dict) -> None:
    signals = flatten_record(raw_record).org_signals

    sources = set(signals["source"].to_list())

    assert {"attribution", "contact_email", "domain_metadata", "domain_tag", "owner", "creator"} <= sources
    assert signals.filter(pl.col("value").is_null()).height == 0


def test_parents_only_appear_for_derived_assets(raw_record: dict) -> None:
    parents = flatten_record(raw_record).asset_parents

    assert parents.select("asset_id", "parent_id").rows() == [("bbbb-0002", "aaaa-0001")]


def test_payload_may_arrive_as_json_text(raw_record: dict) -> None:
    record = {**raw_record, "payload": json.dumps(raw_record["payload"])}

    assert flatten_record(record).assets.height == 2


def test_empty_payload_is_an_error(raw_record: dict) -> None:
    with pytest.raises(ValueError, match="empty payload"):
        flatten_record({**raw_record, "payload": []})
