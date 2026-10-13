from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..blobs import FilesystemBlobStore
from ..domain import FetchRef, FetchSummary
from ..models import FetchRow


class FetchRepository:
    """The metadata row for each response, plus its body in the blob store."""

    def __init__(self, session: Session, blobs: FilesystemBlobStore) -> None:
        self.session = session
        self.blobs = blobs

    def save(
        self, run_id: int, *, url: str, final_url: str, status: int, headers: Mapping[str, str], body: bytes,
        fetcher: str, duration_ms: int | None = None,
    ) -> FetchRef:
        # The body is written first. If the transaction then rolls back, the blob is an orphan that costs a file, never a row
        # that points at nothing.
        sha256 = self.blobs.put(body)
        row = FetchRow(
            run_id=run_id, url=url, final_url=final_url, status=status, headers=dict(headers), duration_ms=duration_ms,
            fetcher=fetcher, blob_sha256=sha256, size=len(body),
        )
        self.session.add(row)
        self.session.flush()
        return FetchRef(row.id, sha256, len(body))

    def body(self, fetch_id: int) -> bytes:
        sha256 = self.session.scalar(select(FetchRow.blob_sha256).where(FetchRow.id == fetch_id))
        if sha256 is None:
            raise KeyError(fetch_id)
        return self.blobs.get(sha256)

    def since(self, run_id: int, after_id: int = 0, limit: int = 200) -> list[FetchSummary]:
        """The run's archived responses with an id above `after_id`, oldest first. The spiders page asks again with the last id it saw."""
        rows = self.session.execute(
            select(FetchRow.id, FetchRow.url, FetchRow.final_url, FetchRow.status, FetchRow.fetched_at, FetchRow.duration_ms, FetchRow.size)
            .where(FetchRow.run_id == run_id, FetchRow.id > after_id)
            .order_by(FetchRow.id)
            .limit(limit)
        )
        return [FetchSummary(*row) for row in rows]
