"""Alembic, scoped to the `pipeline` schema. The web app owns `auth` and the API owns `app`; neither is ever touched here."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, text

from .models import SCHEMA, Base

HERE = Path(__file__).parent


def include_name(name, type_, parent_names) -> bool:
    """Only the `pipeline` schema and what is inside it. Without this, autogenerate would see the `auth` and `app` tables and
    propose dropping them."""
    if type_ == "schema":
        return name == SCHEMA
    return parent_names.get("schema_name") == SCHEMA


# Shared by env.py and the drift test, so the test checks exactly what a real autogenerate would see.
CONTEXT_OPTIONS = {
    "target_metadata": Base.metadata,
    "include_schemas": True,
    "include_name": include_name,
    "version_table_schema": SCHEMA,
    "compare_type": True,
}


def ensure_schema(connection) -> None:
    """The version table lives in the schema, so the schema must exist before Alembic starts. A database role that only owns the
    schema cannot run CREATE SCHEMA even with IF NOT EXISTS, so it is only created when absent."""
    if connection.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :name"), {"name": SCHEMA}).first() is None:
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))  # SCHEMA is a module constant; an identifier cannot be a bound parameter


def alembic_config(engine: Engine | None = None) -> Config:
    config = Config(str(HERE / "alembic.ini"))
    config.set_main_option("script_location", str(HERE / "migrations"))
    if engine is not None:
        config.attributes["engine"] = engine
    return config


def upgrade(engine: Engine, revision: str = "head") -> None:
    command.upgrade(alembic_config(engine), revision)


def downgrade(engine: Engine, revision: str) -> None:
    command.downgrade(alembic_config(engine), revision)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection, opts={"version_table_schema": SCHEMA}).get_current_revision()
