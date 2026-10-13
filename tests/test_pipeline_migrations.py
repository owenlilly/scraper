"""The migrations are the only thing that creates the schema, so they are tested as hard as the code that uses it."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from scraper.pipeline.migrate import CONTEXT_OPTIONS, alembic_config, current_revision, downgrade, upgrade
from scraper.pipeline.models import SCHEMA, Base


def table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names(schema=SCHEMA))


@pytest.fixture
def scratch(pipeline_engine):
    """The shared schema is torn down and rebuilt around a test that needs to start from nothing."""
    yield pipeline_engine
    with pipeline_engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS pipeline CASCADE"))
    upgrade(pipeline_engine)


def test_upgrading_from_nothing_creates_every_table_and_records_the_revision(scratch):
    with scratch.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS pipeline CASCADE"))
    upgrade(scratch)
    assert table_names(scratch) == {"source", "run", "fetch", "record", "record_version", "alembic_version"}
    assert current_revision(scratch) == ScriptDirectory.from_config(alembic_config()).get_current_head()


def test_upgrading_again_changes_nothing(pipeline_engine):
    before = current_revision(pipeline_engine)
    upgrade(pipeline_engine)
    assert current_revision(pipeline_engine) == before


def test_the_models_and_the_migrations_agree(pipeline_engine):
    """If this fails, a model changed without a migration (or the reverse): run `make pipeline-revision`."""
    with pipeline_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts=CONTEXT_OPTIONS)
        assert compare_metadata(context, Base.metadata) == []


def test_every_migration_can_be_undone(scratch):
    downgrade(scratch, "base")
    assert table_names(scratch) <= {"alembic_version"}
    upgrade(scratch)
    assert "record_version" in table_names(scratch)


def test_there_is_exactly_one_head(pipeline_engine):
    assert len(ScriptDirectory.from_config(alembic_config()).get_heads()) == 1


def test_migrations_never_touch_other_schemas(pipeline_engine):
    """Autogenerate must not see `app` or `auth` tables, or it would propose dropping them."""
    with pipeline_engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
        connection.execute(text("CREATE TABLE IF NOT EXISTS app.someone_elses_table (id integer)"))
    try:
        with pipeline_engine.connect() as connection:
            context = MigrationContext.configure(connection, opts=CONTEXT_OPTIONS)
            assert compare_metadata(context, Base.metadata) == []
    finally:
        with pipeline_engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS app.someone_elses_table"))
