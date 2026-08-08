-- Destination schema for the data-prep job.
--
-- Every portal_* table below is snapshot-scoped: the job deletes and rewrites
-- one snapshot's rows at a time, so re-running it is idempotent and history
-- accumulates snapshot by snapshot. portal_chunks is the exception -- it is
-- keyed by content hash so an unchanged chunk keeps its embedding.
--
-- Apply with: supabase db execute --file apps/data-prep/sql/001_portal_tables.sql
-- (or paste into the SQL editor).

create extension if not exists vector;

-- ---------------------------------------------------------------- snapshots

create table if not exists portal_snapshots (
    snapshot_id  text primary key,
    domain       text not null,
    scraped_at   timestamptz not null,
    n_assets     integer not null,
    flattened_at timestamptz not null default now()
);

create index if not exists portal_snapshots_domain_scraped_idx
    on portal_snapshots (domain, scraped_at desc);

-- ------------------------------------------------------------------- assets

create table if not exists portal_assets (
    snapshot_id         text not null references portal_snapshots (snapshot_id) on delete cascade,
    asset_id            text not null,
    domain              text,
    name                text,
    type                text,
    description         text,
    attribution         text,
    attribution_link    text,
    contact_email       text,
    domain_category     text,
    license             text,
    provenance          text,
    backend             text,
    lens_view_type      text,
    lens_display_type   text,
    blob_mime_type      text,
    resource_name       text,
    locked              boolean,
    hide_from_data_json boolean,
    download_count      bigint,
    updated_at          timestamptz,
    created_at          timestamptz,
    metadata_updated_at timestamptz,
    data_updated_at     timestamptz,
    publication_date    timestamptz,
    permalink           text,
    link                text,
    owner_id            text,
    owner_name          text,
    creator_id          text,
    creator_name        text,
    n_columns           integer,
    n_parents           integer,
    n_tags              integer,
    primary key (snapshot_id, asset_id)
);

create index if not exists portal_assets_asset_idx on portal_assets (asset_id);
create index if not exists portal_assets_attribution_idx on portal_assets (attribution);
create index if not exists portal_assets_data_updated_idx on portal_assets (data_updated_at);

-- ------------------------------------------------------------------- people

create table if not exists portal_people (
    snapshot_id  text not null references portal_snapshots (snapshot_id) on delete cascade,
    person_id    text not null,
    display_name text,
    user_type    text,
    n_owned      integer,
    n_created    integer,
    primary key (snapshot_id, person_id)
);

create table if not exists portal_asset_people (
    snapshot_id  text not null references portal_snapshots (snapshot_id) on delete cascade,
    asset_id     text not null,
    role         text not null,
    person_id    text,
    display_name text,
    user_type    text,
    primary key (snapshot_id, asset_id, role)
);

create index if not exists portal_asset_people_person_idx on portal_asset_people (person_id);

-- -------------------------------------------------------------- asset detail

create table if not exists portal_page_views (
    snapshot_id               text not null references portal_snapshots (snapshot_id) on delete cascade,
    asset_id                  text not null,
    page_views_last_week      bigint,
    page_views_last_month     bigint,
    page_views_total          bigint,
    page_views_last_week_log  double precision,
    page_views_last_month_log double precision,
    page_views_total_log      double precision,
    primary key (snapshot_id, asset_id)
);

create table if not exists portal_asset_columns (
    snapshot_id text not null references portal_snapshots (snapshot_id) on delete cascade,
    asset_id    text not null,
    position    integer not null,
    column_name text,
    field_name  text,
    datatype    text,
    description text,
    format      jsonb,
    primary key (snapshot_id, asset_id, position)
);

create index if not exists portal_asset_columns_field_idx on portal_asset_columns (field_name);

create table if not exists portal_asset_tags (
    snapshot_id text not null references portal_snapshots (snapshot_id) on delete cascade,
    asset_id    text not null,
    tag         text not null,
    primary key (snapshot_id, asset_id, tag)
);

create table if not exists portal_domain_metadata (
    snapshot_id    text not null references portal_snapshots (snapshot_id) on delete cascade,
    asset_id       text not null,
    key            text not null,
    value          text,
    metadata_group text,
    metadata_field text,
    primary key (snapshot_id, asset_id, key)
);

create index if not exists portal_domain_metadata_key_value_idx on portal_domain_metadata (key, value);

-- `label` alone doesn't identify a link: additional_access_points can carry
-- more than one entry with the same format label (e.g. two application/zip
-- links), so `url` -- the actual distinguishing part -- is in the key too.
create table if not exists portal_access_points (
    snapshot_id text not null references portal_snapshots (snapshot_id) on delete cascade,
    asset_id    text not null,
    source      text not null,
    label       text not null,
    url         text not null,
    title       text,
    description text,
    primary key (snapshot_id, asset_id, source, label, url)
);

create table if not exists portal_asset_parents (
    snapshot_id text not null references portal_snapshots (snapshot_id) on delete cascade,
    asset_id    text not null,
    parent_id   text not null,
    primary key (snapshot_id, asset_id, parent_id)
);

-- Long table of every field that hints at which org owns an asset. No natural
-- key -- the job replaces the snapshot's rows wholesale.
create table if not exists portal_org_signals (
    id          bigint generated always as identity primary key,
    snapshot_id text not null references portal_snapshots (snapshot_id) on delete cascade,
    asset_id    text not null,
    source      text not null,
    key         text,
    value       text
);

create index if not exists portal_org_signals_snapshot_idx on portal_org_signals (snapshot_id);
create index if not exists portal_org_signals_value_idx on portal_org_signals (source, value);

-- ------------------------------------------------------------------ changes

create table if not exists portal_asset_changes (
    id               bigint generated always as identity primary key,
    from_snapshot_id text,
    to_snapshot_id   text not null,
    entity           text not null,   -- asset | column | tag
    entity_key       text not null,
    asset_id         text,
    change_type      text not null,   -- added | removed | modified
    field            text,
    old_value        text,
    new_value        text,
    detected_at      timestamptz not null default now()
);

create index if not exists portal_asset_changes_to_snapshot_idx on portal_asset_changes (to_snapshot_id);
create index if not exists portal_asset_changes_asset_idx on portal_asset_changes (asset_id);
create index if not exists portal_asset_changes_type_idx on portal_asset_changes (entity, change_type);

-- ------------------------------------------------------------------- chunks

-- Keyed by content hash: identical text across snapshots is one row, so the
-- embedding survives a re-run. Rows with embedding is null are the work queue
-- for the embedding step.
create table if not exists portal_chunks (
    content_hash        text primary key,
    asset_id            text not null,
    chunk_index         integer not null,
    kind                text not null,   -- overview | schema
    content             text not null,
    n_chars             integer,
    metadata            jsonb,
    first_seen_snapshot text,
    last_seen_snapshot  text,
    embedding           vector(1536),
    embedded_at         timestamptz,
    created_at          timestamptz not null default now()
);

create index if not exists portal_chunks_asset_idx on portal_chunks (asset_id);
create index if not exists portal_chunks_last_seen_idx on portal_chunks (last_seen_snapshot);
create index if not exists portal_chunks_pending_idx on portal_chunks (embedded_at) where embedding is null;

-- Swap for an hnsw index once the table is populated:
-- create index portal_chunks_embedding_idx on portal_chunks
--     using hnsw (embedding vector_cosine_ops);

-- ------------------------------------------------------------------- grants

-- New tables aren't covered by any pre-existing default-privilege grant, so
-- the data-prep job's service_role key can't read or write them until this
-- runs (PostgREST returns 42501 permission denied otherwise).
grant select, insert, update, delete on
    portal_snapshots,
    portal_assets,
    portal_people,
    portal_asset_people,
    portal_page_views,
    portal_asset_columns,
    portal_asset_tags,
    portal_domain_metadata,
    portal_access_points,
    portal_asset_parents,
    portal_org_signals,
    portal_asset_changes,
    portal_chunks
to service_role;

grant usage, select on all sequences in schema public to service_role;
