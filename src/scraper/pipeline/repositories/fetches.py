from collections.abc import Mapping

from psycopg import Connection
from psycopg.types.json import Jsonb

from ..blobs import FilesystemBlobStore
from ..domain import FetchRef


class FetchRepository:
    """The metadata row for each response, plus its body in the blob store."""

    def __init__(self, conn: Connection, blobs: FilesystemBlobStore) -> None:
        self.conn = conn
        self.blobs = blobs

    def save(
        self, run_id: int, *, url: str, final_url: str, status: int, headers: Mapping[str, str], body: bytes,
        fetcher: str, duration_ms: int | None = None,
    ) -> FetchRef:
        # The body is written first. If the transaction then rolls back, the blob is an orphan that costs a file, never a row
        # that points at nothing.
        sha256 = self.blobs.put(body)
        row = self.conn.execute(
            "INSERT INTO pipeline.fetch (run_id, url, final_url, status, headers, duration_ms, fetcher, blob_sha256, size) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (run_id, url, final_url, status, Jsonb(dict(headers)), duration_ms, fetcher, sha256, len(body)),
        ).fetchone()
        return FetchRef(row[0], sha256, len(body))

    def body(self, fetch_id: int) -> bytes:
        row = self.conn.execute("SELECT blob_sha256 FROM pipeline.fetch WHERE id = %s", (fetch_id,)).fetchone()
        if row is None:
            raise KeyError(fetch_id)
        return self.blobs.get(row[0])
