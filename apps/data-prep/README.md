# data-prep

Turns the raw catalog JSON that `metadata-extract` drops in Supabase into
tidy tables, change events, and RAG chunks.

```
raw_metadata (one JSON blob per scrape)
  -> flatten      eleven tidy frames, one entity each
  -> replace      portal_* tables for this snapshot
  -> diff         portal_asset_changes vs the previous snapshot
  -> chunk + hash portal_chunks, new hashes left unembedded
```

## Setup

Apply the schema once:

```bash
supabase db execute --file apps/data-prep/sql/001_portal_tables.sql
```

## Run

```bash
uv run python -m data_prep.main                      # most recent scrape
uv run python -m data_prep.main --snapshot-id <uuid> # a specific one
uv run python -m data_prep.main --dry-run            # flatten and report only
```

Needs `SUPABASE_URL` and `SUPABASE_KEY`; `PORTAL_DOMAIN` defaults to
`data.ct.gov`. Nightly in CI via `.github/workflows/data-prep.yml`.

## Tables

| table | grain |
| --- | --- |
| `portal_snapshots` | one row per scrape |
| `portal_assets` | one row per asset (dataset, chart, story, ...) |
| `portal_people` | one row per portal user |
| `portal_asset_people` | asset ↔ person, with role (owner / creator) |
| `portal_page_views` | traffic counters, kept off the wide asset row |
| `portal_asset_columns` | one row per column of a tabular asset |
| `portal_asset_tags` | one row per domain tag |
| `portal_domain_metadata` | one row per custom key/value pair |
| `portal_access_points` | one row per external link / download format |
| `portal_asset_parents` | derived asset → parent dataset |
| `portal_org_signals` | long table of every field hinting at ownership |
| `portal_asset_changes` | field-level diff between consecutive snapshots |
| `portal_chunks` | RAG chunks, keyed by content hash |

Every `portal_*` table except `portal_chunks` is snapshot-scoped and rewritten
wholesale for the snapshot being processed, so re-running the job is
idempotent and history accumulates snapshot by snapshot.

## Two design notes

**Ownership is a long table, not a column.** Portals label the publishing
agency inconsistently — attribution string on one asset, an `Agency_*` custom
field on the next, only a tag on a third. `portal_org_signals` collects every
such field as `(source, key, value)` rows so a downstream step can decide
which sources it trusts instead of betting on one column.

**Chunks are keyed by content hash.** A chunk whose text did not change keeps
its row, and therefore its embedding; only genuinely new text lands with
`embedding is null`, which is the work queue for the embedding step. Volatile
counters (page views, downloads) are excluded from the diff so they cannot
churn either the change log or the index.
