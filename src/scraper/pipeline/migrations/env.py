"""Alembic environment. The engine comes from, in order: the caller (`scraper.pipeline.migrate.upgrade`), `-x url=...`, or the
SCRAPER_DATABASE_URL setting the rest of the app uses."""

from alembic import context
from sqlalchemy import text

from scraper.config import load
from scraper.pipeline.db import engine_url, make_engine
from scraper.pipeline.migrate import CONTEXT_OPTIONS, ensure_schema

config = context.config
MIGRATION_LOCK = 7_302_210_001  # any constant: "the pipeline's migrations are running"


def _url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or load().database_url
    if not url:
        raise SystemExit("Set SCRAPER_DATABASE_URL, or pass -x url=postgresql://...")
    return url


def run_migrations_offline() -> None:
    """Write the SQL instead of running it (`alembic upgrade head --sql`)."""
    context.configure(url=engine_url(_url()), literal_binds=True, dialect_opts={"paramstyle": "named"}, **CONTEXT_OPTIONS)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = config.attributes.get("engine")
    owned = engine is None
    if owned:
        engine = make_engine(_url(), pool_size=1)
    try:
        with engine.connect() as connection:
            # The API and the worker both migrate when they start, and on a fresh volume they start together. One at a time: the
            # second waits, then finds nothing left to do. A session lock, released below because a pooled connection outlives this.
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK})
            try:
                ensure_schema(connection)
                connection.commit()
                context.configure(connection=connection, **CONTEXT_OPTIONS)
                with context.begin_transaction():
                    context.run_migrations()
                connection.commit()
            finally:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK})
                connection.commit()
    finally:
        if owned:
            engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
