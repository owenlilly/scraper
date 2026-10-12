"""The `pipeline` schema. Workers write it; the API reads it."""

from psycopg_pool import ConnectionPool

TABLES = """
CREATE TABLE IF NOT EXISTS pipeline.source (
    id bigserial PRIMARY KEY, name text NOT NULL UNIQUE, enabled boolean NOT NULL DEFAULT true, schedule text,
    settings jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS pipeline.run (
    id bigserial PRIMARY KEY, source_id bigint NOT NULL REFERENCES pipeline.source (id), status text NOT NULL DEFAULT 'queued',
    queued_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz, worker_id text,
    stats jsonb NOT NULL DEFAULT '{}', error text);
CREATE INDEX IF NOT EXISTS run_source ON pipeline.run (source_id, id DESC);
CREATE TABLE IF NOT EXISTS pipeline.fetch (
    id bigserial PRIMARY KEY, run_id bigint NOT NULL REFERENCES pipeline.run (id), url text NOT NULL, final_url text NOT NULL,
    status integer NOT NULL, headers jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(), duration_ms integer,
    fetcher text NOT NULL, blob_sha256 text NOT NULL, size integer NOT NULL);
CREATE INDEX IF NOT EXISTS fetch_run ON pipeline.fetch (run_id, id);
CREATE INDEX IF NOT EXISTS fetch_blob ON pipeline.fetch (blob_sha256);
CREATE TABLE IF NOT EXISTS pipeline.record (
    id bigserial PRIMARY KEY, source_id bigint NOT NULL REFERENCES pipeline.source (id), natural_key text NOT NULL,
    current_version_id bigint, first_seen timestamptz NOT NULL DEFAULT now(), last_seen timestamptz NOT NULL DEFAULT now(),
    gone_at timestamptz, UNIQUE (source_id, natural_key));
CREATE TABLE IF NOT EXISTS pipeline.record_version (
    id bigserial PRIMARY KEY, record_id bigint NOT NULL REFERENCES pipeline.record (id), run_id bigint NOT NULL REFERENCES pipeline.run (id),
    fetch_id bigint REFERENCES pipeline.fetch (id), data jsonb NOT NULL, content_hash text NOT NULL, parser_version text NOT NULL,
    seen_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS record_version_record ON pipeline.record_version (record_id, id);
"""
TABLE_NAMES = ("record_version", "record", "fetch", "run", "source")  # children first, so a TRUNCATE or a delete order is safe


def migrate(pool: ConnectionPool) -> None:
    """Create the `pipeline` schema if it is missing, then the tables. As in `scraper.api.db.migrate`, a role that only owns
    the schema cannot run CREATE SCHEMA even with IF NOT EXISTS, so it is only created when absent."""
    with pool.connection() as conn:
        if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'pipeline'").fetchone() is None:
            conn.execute("CREATE SCHEMA pipeline")
        conn.execute(TABLES)
