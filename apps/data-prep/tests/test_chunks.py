"""Building RAG chunks and deciding what needs re-embedding."""

import json
from copy import deepcopy

import polars as pl
from data_prep.chunks import build_chunks, changed_chunks
from data_prep.flatten import flatten_record


def test_every_asset_gets_an_overview_chunk(raw_record: dict) -> None:
    chunks = build_chunks(flatten_record(raw_record))

    overviews = chunks.filter(pl.col("kind") == "overview")

    assert set(overviews["asset_id"]) == {"aaaa-0001", "bbbb-0002"}
    assert overviews["chunk_index"].unique().to_list() == [0]


def test_overview_carries_the_facts_a_question_would_ask_for(raw_record: dict) -> None:
    chunks = build_chunks(flatten_record(raw_record))

    content = chunks.filter(pl.col("asset_id") == "aaaa-0001")["content"][0]

    assert "Lead Screening" in content
    assert "CT Department of Public Health" in content
    assert "Ada Lovelace" in content
    assert "Agency_Agency: Department of Public Health" in content
    assert "Data last updated: 2026-01-02" in content


def test_schema_chunks_split_wide_tables(raw_record: dict) -> None:
    chunks = build_chunks(flatten_record(raw_record), columns_per_chunk=1)

    schema = chunks.filter((pl.col("asset_id") == "aaaa-0001") & (pl.col("kind") == "schema"))

    assert schema.height == 2
    assert schema["chunk_index"].to_list() == [1, 2]
    assert "Column 0" in schema["content"][0]
    assert "Column 1" in schema["content"][1]


def test_metadata_is_json_for_the_vector_store(raw_record: dict) -> None:
    chunks = build_chunks(flatten_record(raw_record))

    metadata = json.loads(chunks["metadata"][0])

    assert metadata["asset_id"] == chunks["asset_id"][0]
    assert metadata["domain"] == "data.ct.gov"
    assert "health" in metadata["tags"]


def test_hash_is_stable_across_runs(raw_record: dict) -> None:
    first = build_chunks(flatten_record(raw_record))
    second = build_chunks(flatten_record({**raw_record, "id": "snapshot-2"}))

    assert first["content_hash"].to_list() == second["content_hash"].to_list()


def test_only_new_text_needs_embedding(raw_record: dict, next_record: dict) -> None:
    before = build_chunks(flatten_record(raw_record))
    after = build_chunks(flatten_record(next_record))

    fresh = changed_chunks(after, before["content_hash"].to_list())

    # The renamed asset's chunks plus the new asset's; the removed asset is
    # simply absent from the new snapshot.
    assert set(fresh["asset_id"]) == {"aaaa-0001", "cccc-0003"}


def test_unchanged_assets_keep_their_embedding(raw_record: dict) -> None:
    edited = deepcopy(raw_record)
    edited["id"] = "snapshot-2"
    edited["payload"][0]["resource"]["description"] = "Rewritten description."

    before = build_chunks(flatten_record(raw_record))
    after = build_chunks(flatten_record(edited))

    fresh = changed_chunks(after, before["content_hash"].to_list())

    assert set(fresh["asset_id"]) == {"aaaa-0001"}
    assert fresh.height < after.height


def test_no_known_hashes_means_embed_everything(raw_record: dict) -> None:
    chunks = build_chunks(flatten_record(raw_record))

    assert changed_chunks(chunks, []).height == chunks.height
