"""One transaction, and the repositories that share it."""

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from .blobs import FilesystemBlobStore
from .repositories import FetchRepository, RecordRepository, RunRepository, SourceRepository


class UnitOfWork:
    """`with UnitOfWork(engine, blobs) as uow: uow.records.upsert_batch(...); uow.runs.finish(...)`. Commits on a clean exit and
    rolls back on an exception, so a batch of records and the run's progress land together or not at all.

    A unit of work is for one thread: spiders open one inside each `asyncio.to_thread` call and never share it."""

    def __init__(self, engine: Engine, blobs: FilesystemBlobStore) -> None:
        self.engine = engine
        self.blobs = blobs

    def __enter__(self) -> "UnitOfWork":
        self.session = Session(self.engine, expire_on_commit=False)
        self.sources = SourceRepository(self.session)
        self.runs = RunRepository(self.session)
        self.fetches = FetchRepository(self.session, self.blobs)
        self.records = RecordRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        finally:
            self.session.close()
