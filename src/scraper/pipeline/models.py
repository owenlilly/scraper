"""The `pipeline` schema as SQLAlchemy models. Alembic builds its migrations from these, and the repositories query through them.

These are persistence models: they never leave a repository. Repositories return the frozen dataclasses in `domain.py`."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, MetaData, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "pipeline"
# Explicit names, so a migration never depends on what Postgres or SQLAlchemy would have called a constraint.
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceRow(Base):
    __tablename__ = "source"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    schedule: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _now()


class RunRow(Base):
    __tablename__ = "run"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.source.id"))
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    queued_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_run_source_id_id", "source_id", "id"),)


class FetchRow(Base):
    __tablename__ = "fetch"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"))
    url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer)
    headers: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = _now()
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    fetcher: Mapped[str] = mapped_column(Text)
    blob_sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)

    __table_args__ = (Index("ix_fetch_run_id_id", "run_id", "id"), Index("ix_fetch_blob_sha256", "blob_sha256"))


class RecordRow(Base):
    __tablename__ = "record"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.source.id"))
    natural_key: Mapped[str] = mapped_column(Text)
    current_version_id: Mapped[int | None] = mapped_column(BigInteger)  # no foreign key: the version points back at its record
    first_seen: Mapped[datetime] = _now()
    last_seen: Mapped[datetime] = _now()
    gone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("source_id", "natural_key", name="uq_record_source_id_natural_key"),)


class RecordVersionRow(Base):
    __tablename__ = "record_version"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.record.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"))
    fetch_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.fetch.id"))
    data: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(Text)
    seen_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_record_version_record_id_id", "record_id", "id"),)


# Children before parents, so deleting in this order never breaks a foreign key.
TABLE_NAMES = tuple(table.name for table in reversed(Base.metadata.sorted_tables))
