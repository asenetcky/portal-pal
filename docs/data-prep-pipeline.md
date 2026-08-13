# The data-prep pipeline, step by step

`apps/data-prep` turns one raw catalog scrape into tidy tables, a change log,
and the chunks a RAG retriever searches over. This walks through what
`data_prep.main.run()` actually does, in order, with an emphasis on the
chunking and re-embedding strategy since that's the part with the least
obvious "why."

```
raw_metadata (one JSON blob per scrape)
    -> flatten        : eleven tidy frames
    -> replace         portal_* tables for this snapshot
    -> diff             portal_asset_changes vs the previous snapshot
    -> chunk + hash    : portal_chunks, new hashes left unembedded
```

Source: `apps/data-prep/src/data_prep/`. Entry point: `main.run()`.

## 1. Where the raw data comes from

`apps/metadata-extract` runs first. It hits a Socrata open-data portal's
discovery API (e.g. `data.ct.gov`), pulls back the *entire* catalog as a list
of dicts — every dataset, chart, map, and story the portal has — and upserts
one row into `raw_metadata`: `{id, domain, timestamp, payload}`. `payload` is
that whole list, untouched. One row in this table is one full scrape.

`data-prep` reads from `raw_metadata` and never talks to the portal directly.

## 2. Fetching a snapshot

`io.fetch_raw_snapshot(client, domain, snapshot_id)` grabs either a specific
scrape by id or, if none is given, the most recent one by `timestamp`. This
is the only network call to Supabase before flattening starts — everything
downstream works on the in-memory payload.

## 3. Flattening one blob into eleven tables

`flatten_record()` (`flatten.py`) is the normalization step: it takes the
nested catalog blob and splits it along its logical seams so each resulting
frame answers one kind of question, instead of one blob mixing eleven
entities together.

| frame | grain | answers |
| --- | --- | --- |
| `snapshot` | 1 row | metadata about this scrape |
| `assets` | 1 / asset | the wide, scalar-only row per dataset/chart/story |
| `people` | 1 / user | distinct portal users, with owned/created counts |
| `asset_people` | 1 / role | asset ↔ person bridge (owner, creator) |
| `page_views` | 1 / asset | traffic counters, kept off the wide row |
| `asset_columns` | 1 / column | schema of tabular assets |
| `asset_tags` | 1 / tag | exploded tag list |
| `domain_metadata` | 1 / pair | custom key/value metadata |
| `access_points` | 1 / link | external links and download formats |
| `asset_parents` | 1 / link | derived assets (charts, maps) → parent dataset |
| `org_signals` | 1 / signal | every field that hints at who owns an asset |

A couple of things make this hold up across portals that don't all send the
same shape of payload:

- **Missing keys become typed nulls**, not errors. A portal with no
  `creator` section or no `access_points` still produces frames with the
  same columns as one that has them.
- **`org_signals` is deliberately a long table, not a column.** Portals label
  the publishing agency inconsistently — an attribution string on one asset,
  a custom `Agency_*` metadata field on the next, only a tag on a third.
  Rather than guess which field is authoritative, every candidate field is
  collected as `(source, key, value)` rows so a downstream consumer can
  decide which sources it trusts.

## 4. Diffing against the previous snapshot

`main.run()` looks up the snapshot that was flattened just before this one
(`io.previous_snapshot_id`) and reads back only what's needed to diff it —
`assets`, `asset_columns`, `asset_tags`. `changes.diff_snapshots()` then
produces `added` / `removed` / `modified` rows per entity: which assets
appeared or vanished, which tracked fields changed value, which tags were
added or dropped.

Only a curated field list is tracked (`TRACKED_ASSET_FIELDS`,
`TRACKED_COLUMN_FIELDS`) — things like name, description, owner, license,
column type. Volatile counters like page views and download counts are
excluded on purpose: they change on every single scrape and would drown out
every real signal in the change log.

This produces `portal_asset_changes`, an audit trail of "what moved since
last time." It's independent of chunking — useful for monitoring even if you
never touch the RAG side.

## 5. Chunking for RAG

This is the part worth understanding in detail, because the split isn't a
generic text splitter — there's no fixed character or token window. The
boundaries are semantic: one asset, then one batch of columns.

`build_chunks()` (`chunks.py`) works in two passes:

**Assemble context per asset.** Tags, custom metadata pairs, access-point
links, and columns are each aggregated and left-joined onto `assets`, so
every asset ends up as one row carrying everything needed to write its
documents.

**Emit one or more chunks per asset:**

- Exactly **one `overview` chunk**, always — a markdown document with name,
  type, publisher, owner, category, tags, contact, license, key dates,
  column count, and a link, plus optional `## Description`,
  `## Portal metadata`, and `## Access points` sections if the asset has
  them.
- **Zero or more `schema` chunks**, only for tabular assets — columns are
  batched into windows of 30 (`COLUMNS_PER_CHUNK`), each window becoming its
  own `name | datatype | description` reference document. A 90-column
  dataset produces one overview chunk plus three schema chunks.

Chunk text is plain markdown on purpose: it's cheap to tokenize and it reads
well when it's surfaced back to a user as a citation, instead of a dump of
raw JSON.

Each chunk also carries a small metadata payload (`kind`, `asset_id`, `name`,
`type`, `domain`, `owner`, `permalink`, `tags`, ...) as JSON, meant for
filtering in the vector store rather than for embedding.

## 6. Hash-based re-embedding — the reason for flattening in the first place

Every chunk's primary key is `content_hash`: the SHA-256 of its own text
(`chunks.content_hash`). This is what makes re-running the pipeline on a new
scrape cheap:

- Chunk text is fully deterministic — same asset content in, same markdown
  out — so unchanged text hashes identically across snapshots.
- `changed_chunks()` filters a snapshot's chunks down to the hashes **not
  already present** in `portal_chunks`. Those are the only ones that need to
  go through an embedding model.
- Everything else is "seen before": its row (and therefore its existing
  embedding) is left alone, and only `last_seen_snapshot` is bumped.

That's the payoff for flattening before indexing: diffing two ~10 MB JSON
blobs for "did this text change" would be slow and approximate, while
diffing two sets of hashes is exact and nearly free. It also means the cost
of a scrape tracks *what changed*, not the size of the catalog — a portal
with 10,000 assets where 12 changed only needs 12–40 new chunks embedded,
not 10,000.

## 7. Writing back to Supabase

Two different write patterns, matched to two different lifetimes:

- **Snapshot-scoped tables** (`portal_assets`, `portal_asset_columns`, ...
  everything except `portal_chunks`) are deleted-then-inserted per
  `snapshot_id`, so re-running the job on the same snapshot is idempotent
  and history simply accumulates snapshot by snapshot.
- **`portal_chunks`** is upserted on `content_hash`. Fresh chunks get
  inserted with `first_seen_snapshot` and `last_seen_snapshot` set; chunks
  that already existed only get `last_seen_snapshot` touched, in small
  batches — Supabase filters like `.in_("content_hash", batch)` land in the
  request URL rather than the body, so the batch size has to stay well under
  what the gateway's URL length allows.

## What's next

`data-prep` stops once fresh chunks are sitting in `portal_chunks` with no
embedding yet — embedding those and querying them at answer time is a
separate concern, covered in [`rag-wiring.md`](rag-wiring.md) for how the
backend's LangGraph agent retrieves against them.
