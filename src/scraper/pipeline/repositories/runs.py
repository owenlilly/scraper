from sqlalchemy import func, update
from sqlalchemy.orm import Session

from ..domain import Run
from ..models import RunRow

FINISHED = ("completed", "paused", "failed")


def _run(row: RunRow) -> Run:
    return Run(
        row.id, row.source_id, row.status, row.queued_at, row.started_at, row.finished_at, row.stats, row.error,
        row.kind, row.retry_of, row.requested_by, row.heartbeat_at, row.cancel_requested, row.options,
    )


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(self, source_id: int) -> Run:
        row = RunRow(source_id=source_id)
        self.session.add(row)
        self.session.flush()  # the server fills in the id, the status and the queued time
        return _run(row)

    def start(self, run_id: int, worker_id: str) -> Run:
        return self._update(run_id, status="running", started_at=func.now(), worker_id=worker_id)

    def finish(self, run_id: int, status: str, stats: dict, error: str | None = None) -> Run:
        if status not in FINISHED:
            raise ValueError(f"a run finishes as one of {FINISHED}, not {status!r}")
        return self._update(run_id, status=status, finished_at=func.now(), stats=stats, error=error)

    def get(self, run_id: int) -> Run | None:
        row = self.session.get(RunRow, run_id)
        return _run(row) if row else None

    def _update(self, run_id: int, **values) -> Run:
        row = self.session.scalars(
            update(RunRow).where(RunRow.id == run_id).values(**values).returning(RunRow).execution_options(populate_existing=True)
        ).one()
        return _run(row)
