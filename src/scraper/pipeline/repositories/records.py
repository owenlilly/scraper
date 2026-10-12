from collections.abc import Sequence

from psycopg import Connection
from psycopg.types.json import Jsonb

from ..domain import RecordItem, RecordVersion, UpsertResult

VERSION_COLUMNS = "v.id, v.record_id, v.run_id, v.fetch_id, v.data, v.content_hash, v.parser_version, v.seen_at"


class RecordRepository:
    """Records and their versions. A record is one thing on a site (its natural key); a version is what it looked like when
    it last changed. Seeing the same content again only moves `last_seen`."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def upsert_batch(self, source_id: int, run_id: int, parser_version: str, items: Sequence[RecordItem]) -> UpsertResult:
        result = UpsertResult()
        for item in items:
            result += self._upsert(source_id, run_id, parser_version, item)
        return result

    def _upsert(self, source_id: int, run_id: int, parser_version: str, item: RecordItem) -> UpsertResult:
        # The upsert takes the row lock, so two workers seeing the same record queue up instead of both adding a version.
        # `xmax = 0` is true only for a row this statement inserted.
        record_id, current_version_id, inserted = self.conn.execute(
            "INSERT INTO pipeline.record (source_id, natural_key) VALUES (%s, %s) "
            "ON CONFLICT (source_id, natural_key) DO UPDATE SET last_seen = now(), gone_at = NULL "
            "RETURNING id, current_version_id, (xmax = 0)",
            (source_id, item.natural_key),
        ).fetchone()
        if not inserted:
            current_hash = self.conn.execute(
                "SELECT content_hash FROM pipeline.record_version WHERE id = %s", (current_version_id,)
            ).fetchone()
            if current_hash is not None and current_hash[0] == item.content_hash:
                return UpsertResult(unchanged=1)
        version_id = self.conn.execute(
            "INSERT INTO pipeline.record_version (record_id, run_id, fetch_id, data, content_hash, parser_version) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (record_id, run_id, item.fetch_id, Jsonb(item.data), item.content_hash, parser_version),
        ).fetchone()[0]
        self.conn.execute("UPDATE pipeline.record SET current_version_id = %s WHERE id = %s", (version_id, record_id))
        return UpsertResult(created=1) if inserted else UpsertResult(changed=1)

    def current(self, source_id: int, natural_key: str) -> RecordVersion | None:
        row = self.conn.execute(
            f"SELECT {VERSION_COLUMNS} FROM pipeline.record r "
            "JOIN pipeline.record_version v ON v.id = r.current_version_id WHERE r.source_id = %s AND r.natural_key = %s",
            (source_id, natural_key),
        ).fetchone()
        return RecordVersion(*row) if row else None

    def history(self, source_id: int, natural_key: str) -> list[RecordVersion]:
        rows = self.conn.execute(
            f"SELECT {VERSION_COLUMNS} FROM pipeline.record r "
            "JOIN pipeline.record_version v ON v.record_id = r.id WHERE r.source_id = %s AND r.natural_key = %s ORDER BY v.id",
            (source_id, natural_key),
        ).fetchall()
        return [RecordVersion(*row) for row in rows]

    def count(self, source_id: int) -> int:
        return self.conn.execute("SELECT count(*) FROM pipeline.record WHERE source_id = %s", (source_id,)).fetchone()[0]
