from psycopg import Connection
from psycopg.types.json import Jsonb

from ..domain import Run

COLUMNS = "id, source_id, status, queued_at, started_at, finished_at, stats, error"
FINISHED = ("completed", "paused", "failed")


class RunRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def enqueue(self, source_id: int) -> Run:
        row = self.conn.execute(f"INSERT INTO pipeline.run (source_id) VALUES (%s) RETURNING {COLUMNS}", (source_id,)).fetchone()
        return Run(*row)

    def start(self, run_id: int, worker_id: str) -> Run:
        row = self.conn.execute(
            f"UPDATE pipeline.run SET status = 'running', started_at = now(), worker_id = %s WHERE id = %s RETURNING {COLUMNS}",
            (worker_id, run_id),
        ).fetchone()
        return Run(*row)

    def finish(self, run_id: int, status: str, stats: dict, error: str | None = None) -> Run:
        if status not in FINISHED:
            raise ValueError(f"a run finishes as one of {FINISHED}, not {status!r}")
        row = self.conn.execute(
            f"UPDATE pipeline.run SET status = %s, finished_at = now(), stats = %s, error = %s WHERE id = %s RETURNING {COLUMNS}",
            (status, Jsonb(stats), error, run_id),
        ).fetchone()
        return Run(*row)

    def get(self, run_id: int) -> Run | None:
        row = self.conn.execute(f"SELECT {COLUMNS} FROM pipeline.run WHERE id = %s", (run_id,)).fetchone()
        return Run(*row) if row else None
