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
    from scraper_api.db import make_pool, migrate

    pool = make_pool(pg_url)
    migrate(pool)
    yield pool
    pool.close()


@pytest.fixture
def pg(pg_pool):
    """The migrated pool with every per-user table emptied."""
    from scraper_api.db import USER_TABLES

    with pg_pool.connection() as conn:
        conn.execute("TRUNCATE " + ", ".join(f"app.{t}" for t in USER_TABLES))
    return pg_pool


