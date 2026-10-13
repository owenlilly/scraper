"""The pipeline's database engine."""

from sqlalchemy import Engine, create_engine


def engine_url(url: str) -> str:
    """SQLAlchemy 2.1 defaults a bare `postgresql://` URL to psycopg 3, but older installs default to psycopg2, and the failure
    when the wrong driver is missing is confusing. Name the driver explicitly, whatever form the setting was written in."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str, *, pool_size: int = 5) -> Engine:
    """One engine per process. Sessions borrow connections from its pool; `pool_pre_ping` replaces a connection the server closed."""
    return create_engine(engine_url(url), pool_size=pool_size, pool_pre_ping=True)
