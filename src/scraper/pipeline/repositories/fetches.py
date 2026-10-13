from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..blobs import FilesystemBlobStore
from ..domain import FetchRef
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
