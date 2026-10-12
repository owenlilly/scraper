"""Postgres plumbing for the hosted API: schema, connection pools and the LangGraph checkpointer."""

from contextlib import AsyncExitStack

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

TABLES = """
CREATE TABLE IF NOT EXISTS app.sessions (
    user_id text PRIMARY KEY, session_id text NOT NULL, run_counter integer NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS app.pages (
    user_id text NOT NULL, key text NOT NULL, position integer NOT NULL, url text NOT NULL, title text NOT NULL,
    markdown text NOT NULL, links jsonb NOT NULL, html text NOT NULL, looks_empty boolean NOT NULL,
    PRIMARY KEY (user_id, key));
CREATE INDEX IF NOT EXISTS pages_user_position ON app.pages (user_id, position);
-- json, not jsonb: jsonb sorts an object's keys, and the results table's columns must stay in the order the agent chose
CREATE TABLE IF NOT EXISTS app.rows (id bigserial PRIMARY KEY, user_id text NOT NULL, data json NOT NULL);
CREATE INDEX IF NOT EXISTS rows_user ON app.rows (user_id, id);
CREATE TABLE IF NOT EXISTS app.history (
    id bigserial PRIMARY KEY, user_id text NOT NULL, role text NOT NULL, content text NOT NULL, reasoning text NOT NULL,
    sources jsonb NOT NULL, interrupted boolean NOT NULL, note text NOT NULL, trace jsonb NOT NULL);
CREATE INDEX IF NOT EXISTS history_user ON app.history (user_id, id);
CREATE TABLE IF NOT EXISTS app.runs (user_id text PRIMARY KEY, data jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS app.prefs (
    user_id text NOT NULL, key text NOT NULL, value text NOT NULL, PRIMARY KEY (user_id, key));
CREATE TABLE IF NOT EXISTS app.usage (
    user_id text NOT NULL, day date NOT NULL, model_calls integer NOT NULL DEFAULT 0, pages integer NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day));
"""
ROWS_AS_JSON = """
DO $$ BEGIN
    IF (SELECT data_type FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'rows' AND column_name = 'data') = 'jsonb' THEN
        ALTER TABLE app.rows ALTER COLUMN data TYPE json;
    END IF;
END $$;
"""
USER_TABLES = ("sessions", "pages", "rows", "history", "runs", "prefs", "usage")


def make_pool(url: str, *, max_size: int = 10) -> ConnectionPool:
    """A pool of ordinary connections, opened. Callers close it."""
    pool = ConnectionPool(url, min_size=1, max_size=max_size, open=False)
    pool.open(wait=True, timeout=15)
    return pool


def migrate(pool: ConnectionPool) -> None:
    """Create the `app` schema if it is missing, then the tables. A database role that only owns the schema (the
    deployment setup) cannot run CREATE SCHEMA, even IF NOT EXISTS, so the schema is only created when absent."""
    with pool.connection() as conn:
        if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'app'").fetchone() is None:
            conn.execute("CREATE SCHEMA app")
        conn.execute(TABLES)
        conn.execute(ROWS_AS_JSON)  # a database made before rows were json keeps its rows, with their key order as jsonb left it


async def open_checkpointer(url: str, stack: AsyncExitStack) -> AsyncPostgresSaver:
    """LangGraph's Postgres checkpointer, with its tables in the `app` schema. Run `migrate` first so the schema exists."""
    pool = AsyncConnectionPool(
        url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row, "options": "-c search_path=app"},
    )
    await pool.open(wait=True, timeout=15)
    stack.push_async_callback(pool.close)
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    return saver
