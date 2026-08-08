"""The end-to-end job, driven against an in-memory stand-in for Supabase.

The fake implements just the PostgREST surface `supabase_io` uses, which is
enough to catch the wiring mistakes that matter: rows landing in the wrong
table, a re-run duplicating history, a chunk being re-embedded for no reason.
"""

from typing import Any

import pytest
from data_prep.main import run


class FakeResponse:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data


class FakeQuery:
    """One chained PostgREST call. Work happens on `execute()`."""

    def __init__(self, store: dict[str, list[dict[str, Any]]], table: str) -> None:
        self.store = store
        self.table_name = table
        self.filters: list[tuple[str, str, Any]] = []
        self.action = "select"
        self.payload: list[dict[str, Any]] = []
        self.values: dict[str, Any] = {}
        self.order_by: str | None = None
        self.descending = False
        self.limit_to: int | None = None

    # -- filters and modifiers, all chainable ------------------------------
    def select(self, *_: Any, **__: Any) -> "FakeQuery":
        self.action = "select"
        return self

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self.filters.append(("eq", column, value))
        return self

    def lt(self, column: str, value: Any) -> "FakeQuery":
        self.filters.append(("lt", column, value))
        return self

    def in_(self, column: str, values: list[Any]) -> "FakeQuery":
        self.filters.append(("in", column, values))
        return self

    def order(self, column: str, desc: bool = False) -> "FakeQuery":
        self.order_by, self.descending = column, desc
        return self

    def limit(self, count: int) -> "FakeQuery":
        self.limit_to = count
        return self

    def range(self, start: int, end: int) -> "FakeQuery":
        self.window = (start, end)
        return self

    # -- writes -------------------------------------------------------------
    def insert(self, rows: list[dict[str, Any]]) -> "FakeQuery":
        self.action, self.payload = "insert", rows
        return self

    def upsert(self, rows: list[dict[str, Any]], on_conflict: str | None = None) -> "FakeQuery":
        self.action, self.payload = "upsert", rows
        self.conflict_key = on_conflict
        return self

    def update(self, values: dict[str, Any]) -> "FakeQuery":
        self.action, self.values = "update", values
        return self

    def delete(self) -> "FakeQuery":
        self.action = "delete"
        return self

    # -- execution ----------------------------------------------------------
    def execute(self) -> FakeResponse:
        rows = self.store.setdefault(self.table_name, [])

        if self.action == "insert":
            rows.extend(self.payload)
            return FakeResponse(self.payload)

        if self.action == "upsert":
            key = self.conflict_key or "id"
            for row in self.payload:
                existing = next((r for r in rows if r.get(key) == row.get(key)), None)
                if existing is None:
                    rows.append(row)
                else:
                    existing.update(row)
            return FakeResponse(self.payload)

        if self.action == "delete":
            kept = [row for row in rows if not self._matches(row)]
            removed = len(rows) - len(kept)
            self.store[self.table_name] = kept
            return FakeResponse([{"deleted": removed}])

        if self.action == "update":
            for row in rows:
                if self._matches(row):
                    row.update(self.values)
            return FakeResponse([])

        selected = [row for row in rows if self._matches(row)]
        if self.order_by:
            selected.sort(key=lambda row: row.get(self.order_by) or "", reverse=self.descending)
        if self.limit_to is not None:
            selected = selected[: self.limit_to]

        return FakeResponse(selected)

    def _matches(self, row: dict[str, Any]) -> bool:
        for op, column, value in self.filters:
            actual = row.get(column)
            if op == "eq" and actual != value:
                return False
            if op == "lt" and not (actual is not None and actual < value):
                return False
            if op == "in" and actual not in value:
                return False
        return True


class FakeClient:
    def __init__(self, store: dict[str, list[dict[str, Any]]]) -> None:
        self.store = store

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self.store, name)


@pytest.fixture
def store(raw_record: dict) -> dict[str, list[dict[str, Any]]]:
    return {"raw_metadata": [raw_record]}


@pytest.fixture
def client(store: dict) -> FakeClient:
    return FakeClient(store)


def test_first_run_populates_every_table(store: dict, client: FakeClient, raw_record: dict) -> None:
    report = run(domain="data.ct.gov", client=client)  # ty: ignore[invalid-argument-type]

    assert report["snapshot_id"] == "snapshot-1"
    assert report["previous_snapshot_id"] is None
    assert len(store["portal_snapshots"]) == 1
    assert len(store["portal_assets"]) == 2
    assert len(store["portal_asset_people"]) == 4
    assert {row["asset_id"] for row in store["portal_chunks"]} == {"aaaa-0001", "bbbb-0002"}
    assert store["portal_asset_changes"] == []


def test_timestamps_are_serialized_as_text(store: dict, client: FakeClient) -> None:
    run(domain="data.ct.gov", client=client)  # ty: ignore[invalid-argument-type]

    asset = store["portal_assets"][0]

    assert isinstance(asset["data_updated_at"], str)
    assert asset["data_updated_at"].endswith("Z")


def test_rerunning_the_same_snapshot_is_idempotent(store: dict, client: FakeClient) -> None:
    run(domain="data.ct.gov", client=client)  # ty: ignore[invalid-argument-type]
    run(domain="data.ct.gov", client=client)  # ty: ignore[invalid-argument-type]

    assert len(store["portal_assets"]) == 2
    assert len(store["portal_snapshots"]) == 1


def test_second_snapshot_records_changes_and_reuses_chunks(
    store: dict,
    client: FakeClient,
    next_record: dict,
) -> None:
    run(domain="data.ct.gov", client=client)  # ty: ignore[invalid-argument-type]
    chunks_after_first = len(store["portal_chunks"])

    store["raw_metadata"].append(next_record)
    report = run(domain="data.ct.gov", client=client)  # ty: ignore[invalid-argument-type]

    assert report["previous_snapshot_id"] == "snapshot-1"
    assert report["n_changes"] > 0

    changes = store["portal_asset_changes"]
    assert {row["change_type"] for row in changes} == {"added", "removed", "modified"}

    # Only the genuinely new text was inserted; the rest of the index stands.
    assert len(store["portal_chunks"]) > chunks_after_first
    assert report["n_chunks_to_embed"] < report["n_chunks"] + chunks_after_first


def test_dry_run_writes_nothing(store: dict, client: FakeClient) -> None:
    report = run(domain="data.ct.gov", client=client, dry_run=True)  # ty: ignore[invalid-argument-type]

    assert report["n_assets"] == 2
    assert report["rows_written"] == {}
    assert "portal_assets" not in store
