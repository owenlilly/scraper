from contextlib import contextmanager

from scraper_api.db import migrate


class FakeConn:
    def __init__(self, schema_exists: bool):
        self.schema_exists = schema_exists
        self.statements: list[str] = []

    def execute(self, sql, *args):
        self.statements.append(sql.strip().split("\n")[0][:40])
        outer = self

        class Result:
            def fetchone(self):
                return (1,) if outer.schema_exists else None

        return Result()


class FakePool:
    def __init__(self, schema_exists: bool):
        self.conn = FakeConn(schema_exists)

    @contextmanager
    def connection(self):
        yield self.conn


def test_migrate_creates_the_schema_only_when_it_is_missing():
    missing = FakePool(schema_exists=False)
    migrate(missing)
    assert "CREATE SCHEMA app" in missing.conn.statements
    present = FakePool(schema_exists=True)
    migrate(present)  # a role that owns only the schema cannot run CREATE SCHEMA, even IF NOT EXISTS
    assert "CREATE SCHEMA app" not in present.conn.statements


def test_migrate_can_run_again_against_a_real_database(pg_pool):
    migrate(pg_pool)
    migrate(pg_pool)
