from collections.abc import Sequence
from itertools import batched

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..domain import RecordItem, RecordVersion, UpsertResult
from ..models import RecordRow, RecordVersionRow

CHUNK = 500  # rows per statement: two or six bound values each, far under the driver's limit


def _version(row: RecordVersionRow) -> RecordVersion:
    return RecordVersion(row.id, row.record_id, row.run_id, row.fetch_id, row.data, row.content_hash, row.parser_version, row.seen_at)


class RecordRepository:
    """Records and their versions. A record is one thing on a site (its natural key); a version is what it looked like when
    it last changed. Seeing the same content again only moves `last_seen`."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_batch(self, source_id: int, run_id: int, parser_version: str, items: Sequence[RecordItem]) -> UpsertResult:
        """Set-based: a few statements per chunk of records, not per record. A statement cannot touch the same row twice, so
        if a key repeats within `items` the last one wins and the repeat is not counted."""
        result = UpsertResult()
        for chunk in batched({item.natural_key: item for item in items}.values(), CHUNK):
            result += self._upsert_chunk(source_id, run_id, parser_version, chunk)
        return result

    def _upsert_chunk(self, source_id: int, run_id: int, parser_version: str, chunk: Sequence[RecordItem]) -> UpsertResult:
        # The upsert takes the row locks, so two workers seeing the same record queue up instead of both adding a version.
        # `xmax = 0` is true only for a row this statement inserted.
        seen = self.session.execute(
            insert(RecordRow)
            .values([{"source_id": source_id, "natural_key": item.natural_key} for item in chunk])
            .on_conflict_do_update(
                index_elements=[RecordRow.source_id, RecordRow.natural_key], set_={"last_seen": func.now(), "gone_at": None}
            )
            .returning(RecordRow.id, RecordRow.natural_key, RecordRow.current_version_id, literal_column("(xmax = 0)").label("inserted"))
        ).all()
        by_key = {row.natural_key: row for row in seen}
        known = [row.current_version_id for row in seen if row.current_version_id is not None]
        hashes = dict(self.session.execute(select(RecordVersionRow.id, RecordVersionRow.content_hash).where(RecordVersionRow.id.in_(known))).all()) if known else {}
        to_write = [
            (by_key[item.natural_key], item)
            for item in chunk
            if by_key[item.natural_key].inserted or hashes.get(by_key[item.natural_key].current_version_id) != item.content_hash
        ]
        if to_write:
            written = self.session.execute(
                insert(RecordVersionRow)
                .values([
                    {"record_id": row.id, "run_id": run_id, "fetch_id": item.fetch_id, "data": item.data,
                     "content_hash": item.content_hash, "parser_version": parser_version}
                    for row, item in to_write
                ])
                .returning(RecordVersionRow.id, RecordVersionRow.record_id)
            ).all()
            self.session.execute(update(RecordRow), [{"id": record_id, "current_version_id": version_id} for version_id, record_id in written])
        created = sum(1 for row, _ in to_write if row.inserted)
        return UpsertResult(created=created, changed=len(to_write) - created, unchanged=len(chunk) - len(to_write))

    def current(self, source_id: int, natural_key: str) -> RecordVersion | None:
        row = self.session.scalars(
            select(RecordVersionRow)
            .join(RecordRow, RecordRow.current_version_id == RecordVersionRow.id)
            .where(RecordRow.source_id == source_id, RecordRow.natural_key == natural_key)
        ).one_or_none()
        return _version(row) if row else None

    def history(self, source_id: int, natural_key: str) -> list[RecordVersion]:
        rows = self.session.scalars(
            select(RecordVersionRow)
            .join(RecordRow, RecordRow.id == RecordVersionRow.record_id)
            .where(RecordRow.source_id == source_id, RecordRow.natural_key == natural_key)
            .order_by(RecordVersionRow.id)
        )
        return [_version(row) for row in rows]

    def count(self, source_id: int) -> int:
        return self.session.scalar(select(func.count()).select_from(RecordRow).where(RecordRow.source_id == source_id))
