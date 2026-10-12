import os

import pytest

DATABASE_URL = os.environ.get("SCRAPER_TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def pg_url():
    if not DATABASE_URL:
        pytest.skip("set SCRAPER_TEST_DATABASE_URL to run the Postgres tests")
    return DATABASE_URL


@pytest.fixture(scope="session")
def pg_pool(pg_url):
    from scraper.api.db import make_pool, migrate

    pool = make_pool(pg_url)
    migrate(pool)
    yield pool
    pool.close()


@pytest.fixture
def pg(pg_pool):
    """The migrated pool with every per-user table emptied."""
    from scraper.api.db import USER_TABLES

    with pg_pool.connection() as conn:
        conn.execute("TRUNCATE " + ", ".join(f"app.{t}" for t in USER_TABLES))
    return pg_pool


@pytest.fixture
def pipeline(pg_pool):
    """The migrated pool with every pipeline table emptied."""
    from scraper.pipeline.schema import TABLE_NAMES, migrate

    migrate(pg_pool)
    with pg_pool.connection() as conn:
        conn.execute("TRUNCATE " + ", ".join(f"pipeline.{name}" for name in TABLE_NAMES) + " RESTART IDENTITY")
    return pg_pool


@pytest.fixture
def blobs(tmp_path):
    from scraper.pipeline.blobs import FilesystemBlobStore

    return FilesystemBlobStore(tmp_path / "blobs")


@pytest.fixture
async def api(pg):
    from api_support import Harness

    harness = Harness(pg).build()
    yield harness
    await harness.close()


@pytest.fixture
async def make_api(pg):
    from api_support import Harness

    made = []

    def build(**overrides):
        harness = Harness(pg, **overrides).build()
        made.append(harness)
        return harness

    yield build
    for harness in made:
        await harness.close()


@pytest.fixture(autouse=True)
def _debug_log_off_again():
    """A test that turns debug logging on must not leave it on for the tests after it."""
    yield
    from scraper import debuglog
    from scraper.config import Config

    debuglog.configure(Config())
