from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.orm import Session

from ..domain import Run
from ..models import RunRow

ACTIVE = ("queued", "running")
FINISHED = ("completed", "paused", "failed")
KINDS = ("live", "trial")


class SourceBusy(Exception):
    """The source already has a queued or running run."""


def _run(row: RunRow) -> Run:
    return Run(
        row.id, row.source_id, row.status, row.queued_at, row.started_at, row.finished_at, row.stats, row.error,
        row.kind, row.retry_of, row.requested_by, row.heartbeat_at, row.cancel_requested, row.options,
    )


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self, source_id: int, *, kind: str = "live", retry_of: int | None = None, requested_by: str | None = None, options: dict | None = None
    ) -> Run:
        """Queue a run. Raises `SourceBusy` if the source already has one queued or running: the partial unique index decides, in
        the same statement that inserts, so two simultaneous requests cannot both win."""
        if kind not in KINDS:
            raise ValueError(f"a run is one of {KINDS}, not {kind!r}")
        statement = (
            insert(RunRow)
            .values(source_id=source_id, kind=kind, retry_of=retry_of, requested_by=requested_by, options=options or {})
            .on_conflict_do_nothing(index_elements=[RunRow.source_id], index_where=text("status IN ('queued', 'running')"))
            .returning(RunRow)
        )
        row = self.session.scalars(statement).one_or_none()
        if row is None:
            raise SourceBusy(source_id)
        return _run(row)

    def start(self, run_id: int, worker_id: str) -> Run:
        return self._update(run_id, status="running", started_at=func.now(), heartbeat_at=func.now(), worker_id=worker_id)

    def claim_next(self, worker_id: str) -> Run | None:
        """Hand the oldest queued run to one worker. `SKIP LOCKED` makes a second worker take the next one instead of waiting."""
        oldest = select(RunRow.id).where(RunRow.status == "queued").order_by(RunRow.id).limit(1).with_for_update(skip_locked=True).scalar_subquery()
        row = self.session.scalars(
            update(RunRow)
            .where(RunRow.id == oldest)
            .values(status="running", started_at=func.now(), heartbeat_at=func.now(), worker_id=worker_id)
            .returning(RunRow)
            .execution_options(populate_existing=True)
        ).one_or_none()
        return _run(row) if row else None

    def heartbeat(self, run_id: int, stats: dict) -> bool:
        """Record progress. True means the process should stop: a stop was requested, or the run is no longer running (it was
        reaped, say). Only a running run is written, so a late beat never overwrites the final stats."""
        stop = self.session.scalar(
            update(RunRow).where(RunRow.id == run_id, RunRow.status == "running").values(stats=stats, heartbeat_at=func.now()).returning(RunRow.cancel_requested)
        )
        return True if stop is None else stop

    def request_cancel(self, run_id: int) -> Run | None:
        """A queued run is cancelled at once; a running run is flagged and stops itself at its next beat. None when there is
        nothing to stop (finished or missing)."""
        row = self.session.scalars(
            update(RunRow).where(RunRow.id == run_id, RunRow.status == "queued").values(status="cancelled", finished_at=func.now()).returning(RunRow)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if row is None:
            row = self.session.scalars(
                update(RunRow).where(RunRow.id == run_id, RunRow.status == "running").values(cancel_requested=True).returning(RunRow)
                .execution_options(populate_existing=True)
            ).one_or_none()
        return _run(row) if row else None

    def reap_stale(self, older_than: timedelta) -> list[int]:
        """Fail running runs that have gone quiet: their process is gone."""
        return list(
            self.session.scalars(
                update(RunRow)
                .where(RunRow.status == "running", func.coalesce(RunRow.heartbeat_at, RunRow.started_at) < func.now() - older_than)
                .values(status="failed", finished_at=func.now(), error="worker lost")
                .returning(RunRow.id)
                .execution_options(synchronize_session=False)
            )
        )

    def finish(self, run_id: int, status: str, stats: dict, error: str | None = None) -> Run:
        """End a running run. A run that is no longer running (the reaper failed it, or it was stopped while queued) is left as it
        is and returned: a slow process finishing late must not undo that, or hide that a newer run has since started."""
        if status not in FINISHED:
            raise ValueError(f"a run finishes as one of {FINISHED}, not {status!r}")
        row = self.session.scalars(
            update(RunRow)
            .where(RunRow.id == run_id, RunRow.status == "running")
            .values(status=status, finished_at=func.now(), stats=stats, error=error)
            .returning(RunRow)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if row is not None:
            return _run(row)
        current = self.get(run_id)
        if current is None:
            raise LookupError(f"there is no run {run_id}")
        return current

    def get(self, run_id: int) -> Run | None:
        row = self.session.get(RunRow, run_id)
        return _run(row) if row else None

    def recent(self, source_id: int, limit: int = 30) -> list[Run]:
        rows = self.session.scalars(select(RunRow).where(RunRow.source_id == source_id).order_by(RunRow.id.desc()).limit(limit))
        return [_run(row) for row in rows]

    def latest_by_source(self) -> dict[int, Run]:
        rows = self.session.scalars(select(RunRow).ext(distinct_on(RunRow.source_id)).order_by(RunRow.source_id, RunRow.id.desc()))
        return {row.source_id: _run(row) for row in rows}

    def _update(self, run_id: int, **values) -> Run:
        row = self.session.scalars(
            update(RunRow).where(RunRow.id == run_id).values(**values).returning(RunRow).execution_options(populate_existing=True)
        ).one()
        return _run(row)
