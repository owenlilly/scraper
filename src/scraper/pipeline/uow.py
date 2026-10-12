"""One transaction, and the repositories that share it."""

from psycopg_pool import ConnectionPool

from .blobs import FilesystemBlobStore
from .repositories import FetchRepository, RecordRepository, RunRepository, SourceRepository


class UnitOfWork:
    """`with UnitOfWork(pool, blobs) as uow: uow.records.upsert_batch(...); uow.runs.finish(...)`. Commits on a clean exit and
    rolls back on an exception, so a batch of records and the run's progress land together or not at all."""

    def __init__(self, pool: ConnectionPool, blobs: FilesystemBlobStore) -> None:
        self.pool = pool
        self.blobs = blobs

    def __enter__(self) -> "UnitOfWork":
        self._connection = self.pool.connection()
        conn = self._connection.__enter__()
        self.sources = SourceRepository(conn)
        self.runs = RunRepository(conn)
        self.fetches = FetchRepository(conn, self.blobs)
        self.records = RecordRepository(conn)
        return self

    def __exit__(self, *exc_info) -> bool | None:
        return self._connection.__exit__(*exc_info)
