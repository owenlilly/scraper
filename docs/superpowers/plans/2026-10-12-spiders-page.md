# Spiders Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin-only `/spiders` page in the web app that starts, watches, stops, retries and trial-runs the pipeline's spiders, backed by a Postgres run queue, a worker process and admin-only API routes.

**Architecture:** The page polls FastAPI routes (`/pipeline/*`). The routes read and write `pipeline.run` through repositories and never run a spider. A separate worker claims queued runs (`FOR UPDATE SKIP LOCKED`) and runs each in a child process that heartbeats progress into `run.stats` and watches `run.cancel_requested`. A trial run is an ordinary run with `kind='trial'` that stops at a request cap and stores no records.

**Tech Stack:** Python 3.14, SQLAlchemy 2.1 + Alembic, Scrapling, FastAPI, pytest against real Postgres; Next.js 16, React 19, TanStack Query 5, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-12-spiders-page-design.md` (read it first; this plan implements it).

**Branch:** create `spiders_page` from the current `HEAD` of `realtor_ca_spider` in a worktree (use `superpowers:using-git-worktrees`). The uncommitted realtor files (`src/scraper/pipeline/sources/__init__.py`, `src/scraper/pipeline/sources/spiders/`, `tests/test_pipeline_realtor.py`) are not part of this work; never stage them and never edit `sources/__init__.py`. Stage only the files each task names. Never add a `Co-Authored-By` trailer.

**Test setup (once per shell):**

```bash
make test-db
export SCRAPER_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/scraper_test
```

Python tests: `uv run pytest -q <file>`. Web tests: `cd web && npm test -- <file>` and `npm run typecheck`.

## Global Constraints

- Run statuses are exactly `queued`, `running`, `completed`, `paused`, `failed`, `cancelled`. A stop of a running run ends `paused`; a stop of a queued run ends `cancelled`.
- A source never has two runs queued or running at once (partial unique index plus `SourceBusy`), including a run started from the CLI.
- Poll intervals: run and fetch feed every 2 s while the selected run is queued or running; run history and spider list every 6 s while anything is active.
- Heartbeat every 2 s; a running run with no heartbeat for 60 s is failed with error `worker lost`; the page warns after 15 s of silence and after 30 s queued with no worker.
- Trial runs: default cap 5 requests, allowed 1 to 20; keep at most 25 parsed items; write nothing to `record` or `record_version`; still archive every response.
- Scrapling treats a second `pause()` as a force stop, so `pause()` is called at most once per spider (`stop_once`).
- All pipeline SQL lives in repositories. API routes are plain `def` (threadpool), not `async def`, because the unit of work is synchronous.
- Admin check reads `role` and `banned` from `auth."user"` by user id on every request; the token stays `sub`-only.
- Error shape is the existing `{"error": {"code", "message"}}`. Copy: sentence case, no em dashes, an action keeps one name everywhere ("Run now", "Trial run", "Stop", "Retry").
- No live sites and no live models in tests. Spiders are tested against `tests/pipeline_support.py`'s local server.
- Next.js 16: `params` is a `Promise`; read `web/node_modules/next/dist/docs/` before changing page conventions (per `web/AGENTS.md`).
- Open item 1 of the spec is resolved: Scrapling does not hand per-response timing to `is_blocked`, so `fetch.duration_ms` stays unfilled and the ledger has no duration column.

## Review Focus

Failure modes the spec implies but no task's main tests would reach on their own. Each has a test in the task named.

1. Two people press Run now at the same instant: exactly one run is created and the other gets 409 `source_busy` (Task 2, concurrent enqueue test).
2. The worker or child dies mid-run: the run ends `failed` ("worker lost" from the reaper, or "exited with code N" from the worker) and never stays `running` forever (Tasks 2 and 5).
3. Stop is pressed twice, or on a finished run: the API answers 202 then 202, then 409 `not_active`; Scrapling's `pause()` is called once (Tasks 3, 4 and 6).
4. A scraped value holds a NUL character or is enormous: it must not make `run.stats` unwritable (Postgres rejects NUL in jsonb) or bloat it (Task 3, rejected-sample and clipped-item tests).
5. A spider name or run id that does not exist, or a negative `after` cursor: 404 or 422 with the standard error body, never a 500 (Task 6).

## File Structure

Python (`src/scraper/pipeline/`):
- `models.py`, `domain.py`: add run columns and the `FetchSummary` dataclass.
- `migrations/versions/20261012_0002_run_queue_columns.py`: new migration.
- `repositories/runs.py`, `sources.py`, `fetches.py`, `__init__.py`: queue methods.
- `spider.py`: `RunContext` gains `kind` and `options`; trial mode, `stop_once`, `snapshot`.
- `runner.py`: `run_source` and `execute` (heartbeat thread, cancel).
- `worker.py` (new): `Worker`, `spawn_child`, `exec_run`.
- `__main__.py`: `worker` and `exec-run` commands.

Python API (`src/scraper/api/`):
- `admins.py` (new): `is_admin`.
- `deps.py`: `PipelineAccess`, `Deps.pipeline`.
- `pipeline.py` (new): the `/pipeline` router and `runnable_spiders`.
- `app.py`: mount the router, build the pipeline engine at start-up.

Web (`web/src/`):
- `lib/spiders/types.ts`, `derive.ts`
- `components/spiders/`: `queries.ts` (the hooks; they need `useApi`), `SpidersFrame.tsx`, `SpiderTable.tsx`, `SpiderPage.tsx`, `RunList.tsx`, `RunHeader.tsx`, `RunLedger.tsx`, `TrialResults.tsx`, `StatusPill.tsx`, `useNow.ts`, `spiders.css`
- `app/spiders/page.tsx`, `app/spiders/[name]/page.tsx`
- `proxy.ts` (matcher), `components/workspace/AccountMenu.tsx` (link)

Tests: `tests/test_pipeline_queue.py`, `test_pipeline_trial.py`, `test_pipeline_execution.py`, `test_pipeline_worker.py`, `test_pipeline_cli.py`, `test_api_pipeline.py`; `web/tests/spiders/derive.test.ts`; `web/e2e/specs/spiders.spec.ts`.

---

### Task 1: Run queue columns (models, migration 0002, domain)

**Files:**
- Modify: `src/scraper/pipeline/models.py` (class `RunRow`)
- Modify: `src/scraper/pipeline/domain.py` (`Run`, new `FetchSummary`)
- Create: `src/scraper/pipeline/migrations/versions/20261012_0002_run_queue_columns.py`
- Modify: `src/scraper/pipeline/repositories/runs.py` (`_run` mapping only)
- Test: `tests/test_pipeline_migrations.py`

**Interfaces:**
- Produces: `RunRow` columns `kind`, `retry_of`, `requested_by`, `heartbeat_at`, `cancel_requested`, `options`; `Run` dataclass with the same six fields (defaults `"live"`, `None`, `None`, `None`, `False`, `{}`); `FetchSummary(id, url, final_url, status, fetched_at, duration_ms, size)`; partial unique index `uq_run_source_id_active`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_pipeline_migrations.py`; add `from sqlalchemy.exc import IntegrityError` to its imports)

```python
def test_0002_fails_runs_left_active_and_then_allows_one_active_run_per_source(scratch):
    downgrade(scratch, "0001")
    with scratch.begin() as connection:
        connection.execute(text("INSERT INTO pipeline.source (name) VALUES ('shop')"))
        connection.execute(text("INSERT INTO pipeline.run (source_id, status) SELECT id, 'running' FROM pipeline.source"))
        connection.execute(text("INSERT INTO pipeline.run (source_id, status) SELECT id, 'queued' FROM pipeline.source"))
    upgrade(scratch)
    with scratch.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pipeline.run WHERE status = 'failed'")).scalar() == 2
    with scratch.begin() as connection:
        connection.execute(text("INSERT INTO pipeline.run (source_id) SELECT id FROM pipeline.source"))
    with pytest.raises(IntegrityError):
        with scratch.begin() as connection:
            connection.execute(text("INSERT INTO pipeline.run (source_id) SELECT id FROM pipeline.source"))


def test_a_finished_run_does_not_block_a_new_one_and_new_columns_have_defaults(scratch):
    with scratch.begin() as connection:
        connection.execute(text("INSERT INTO pipeline.source (name) VALUES ('shop')"))
        connection.execute(text("INSERT INTO pipeline.run (source_id, status) SELECT id, 'completed' FROM pipeline.source"))
        connection.execute(text("INSERT INTO pipeline.run (source_id) SELECT id FROM pipeline.source"))
    with scratch.connect() as connection:
        row = connection.execute(text("SELECT kind, retry_of, requested_by, heartbeat_at, cancel_requested, options FROM pipeline.run ORDER BY id DESC LIMIT 1")).one()
    assert tuple(row) == ("live", None, None, None, False, {})
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_pipeline_migrations.py`
Expected: the two new tests FAIL (`downgrade(scratch, "0001")` raises, no such revision).

- [ ] **Step 3: Update the models** in `src/scraper/pipeline/models.py`. In `RunRow`, after the `error` column, add the new columns and replace `__table_args__`:

```python
    kind: Mapped[str] = mapped_column(Text, server_default="live")  # 'live' or 'trial'
    retry_of: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"))
    requested_by: Mapped[str | None] = mapped_column(Text)  # the admin's user id; null for the CLI
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    options: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        Index("ix_run_source_id_id", "source_id", "id"),
        # A source has at most one run in flight. The queue relies on this, so two clicks cannot start two crawls.
        Index("uq_run_source_id_active", "source_id", unique=True, postgresql_where=text("status IN ('queued', 'running')")),
    )
```

- [ ] **Step 4: Update the domain** in `src/scraper/pipeline/domain.py`. Replace the `Run` dataclass and add `FetchSummary` after `FetchRef`:

```python
@dataclass(frozen=True)
class Run:
    id: int
    source_id: int
    status: str
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stats: dict = field(default_factory=dict)
    error: str | None = None
    kind: str = "live"
    retry_of: int | None = None
    requested_by: str | None = None
    heartbeat_at: datetime | None = None
    cancel_requested: bool = False
    options: dict = field(default_factory=dict)
```

```python
@dataclass(frozen=True)
class FetchSummary:
    """One archived response as the spiders page lists it: no body, no headers."""

    id: int
    url: str
    final_url: str
    status: int
    fetched_at: datetime
    duration_ms: int | None
    size: int
```

- [ ] **Step 5: Map the new fields** in `src/scraper/pipeline/repositories/runs.py`, replacing `_run`:

```python
def _run(row: RunRow) -> Run:
    return Run(
        row.id, row.source_id, row.status, row.queued_at, row.started_at, row.finished_at, row.stats, row.error,
        row.kind, row.retry_of, row.requested_by, row.heartbeat_at, row.cancel_requested, row.options,
    )
```

- [ ] **Step 6: Write the migration** `src/scraper/pipeline/migrations/versions/20261012_0002_run_queue_columns.py`:

```python
"""add the run queue columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12 21:40:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The one-active-run rule is new. Anything left queued or running belongs to a process that no longer exists.
    op.execute("UPDATE pipeline.run SET status = 'failed', finished_at = now(), error = 'abandoned before the run queue existed' WHERE status IN ('queued', 'running')")
    op.add_column('run', sa.Column('kind', sa.Text(), server_default='live', nullable=False), schema='pipeline')
    op.add_column('run', sa.Column('retry_of', sa.BigInteger(), nullable=True), schema='pipeline')
    op.add_column('run', sa.Column('requested_by', sa.Text(), nullable=True), schema='pipeline')
    op.add_column('run', sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True), schema='pipeline')
    op.add_column('run', sa.Column('cancel_requested', sa.Boolean(), server_default=sa.text('false'), nullable=False), schema='pipeline')
    op.add_column('run', sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False), schema='pipeline')
    op.create_foreign_key(op.f('fk_run_retry_of_run'), 'run', 'run', ['retry_of'], ['id'], source_schema='pipeline', referent_schema='pipeline')
    op.create_index('uq_run_source_id_active', 'run', ['source_id'], unique=True, schema='pipeline', postgresql_where=sa.text("status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_index('uq_run_source_id_active', table_name='run', schema='pipeline')
    op.drop_constraint(op.f('fk_run_retry_of_run'), 'run', schema='pipeline', type_='foreignkey')
    for column in ('options', 'cancel_requested', 'heartbeat_at', 'requested_by', 'retry_of', 'kind'):
        op.drop_column('run', column, schema='pipeline')
```

- [ ] **Step 7: Run the whole migration and pipeline suite**

Run: `uv run pytest -q tests/test_pipeline_migrations.py tests/test_pipeline_repositories.py tests/test_pipeline_run.py`
Expected: PASS (the drift test `test_the_models_and_the_migrations_agree` and `test_every_migration_can_be_undone` now cover 0002 too). The existing `run_source` still works because `enqueue` has not changed yet.

- [ ] **Step 8: Commit**

```bash
git add src/scraper/pipeline/models.py src/scraper/pipeline/domain.py src/scraper/pipeline/repositories/runs.py src/scraper/pipeline/migrations/versions/20261012_0002_run_queue_columns.py tests/test_pipeline_migrations.py
git commit -m "Add the run queue columns and the one-active-run index (migration 0002)"
```

---

### Task 2: Queue repositories (enqueue, claim, heartbeat, cancel, reap, history, fetch feed)

**Files:**
- Modify: `src/scraper/pipeline/repositories/runs.py` (rewrite)
- Modify: `src/scraper/pipeline/repositories/sources.py`
- Modify: `src/scraper/pipeline/repositories/fetches.py`
- Modify: `src/scraper/pipeline/repositories/__init__.py`
- Test: `tests/test_pipeline_queue.py` (new)

**Interfaces:**
- Consumes: Task 1's `Run`, `FetchSummary`, `RunRow` columns.
- Produces:
  - `SourceBusy(Exception)`, exported from `scraper.pipeline.repositories`.
  - `RunRepository.enqueue(source_id, *, kind="live", retry_of=None, requested_by=None, options=None) -> Run` (raises `SourceBusy`)
  - `.start(run_id, worker_id) -> Run` (now also sets `heartbeat_at`)
  - `.claim_next(worker_id) -> Run | None`
  - `.heartbeat(run_id, stats) -> bool` (True means "stop": cancel requested or the run is no longer running)
  - `.request_cancel(run_id) -> Run | None` (None when the run is finished or missing)
  - `.reap_stale(older_than: timedelta) -> list[int]`
  - `.recent(source_id, limit=30) -> list[Run]`, `.latest_by_source() -> dict[int, Run]`, `.get(run_id)`, `.finish(...)` unchanged
  - `SourceRepository.all() -> list[SourceConfig]`, `.by_id(source_id) -> SourceConfig | None`
  - `FetchRepository.since(run_id, after_id=0, limit=200) -> list[FetchSummary]`

- [ ] **Step 1: Write the failing tests** `tests/test_pipeline_queue.py`

```python
"""The run queue: one active run per source, atomic claiming, heartbeats, stopping and reaping."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import update

from scraper.pipeline.models import RunRow
from scraper.pipeline.repositories import SourceBusy
from scraper.pipeline.uow import UnitOfWork


@pytest.fixture
def uow(pipeline, blobs):
    return lambda: UnitOfWork(pipeline, blobs)


def source(uow, name="shop"):
    with uow() as work:
        return work.sources.ensure(name)


def test_enqueue_records_who_asked_what_kind_and_the_options(uow):
    shop = source(uow)
    with uow() as work:
        first = work.runs.enqueue(shop.id, kind="trial", requested_by="alice", options={"max_requests": 3})
    assert (first.status, first.kind, first.requested_by, first.options) == ("queued", "trial", "alice", {"max_requests": 3})
    assert first.retry_of is None and first.cancel_requested is False and first.heartbeat_at is None


def test_a_source_with_a_run_in_flight_refuses_another_until_it_finishes(uow):
    shop, other = source(uow), source(uow, "other")
    with uow() as work:
        run = work.runs.enqueue(shop.id)
        work.runs.enqueue(other.id)  # a different source is unaffected
    with pytest.raises(SourceBusy):
        with uow() as work:
            work.runs.enqueue(shop.id)
    with uow() as work:
        work.runs.start(run.id, "w")
    with pytest.raises(SourceBusy):
        with uow() as work:
            work.runs.enqueue(shop.id)
    with uow() as work:
        work.runs.finish(run.id, "completed", {})
        assert work.runs.enqueue(shop.id, retry_of=run.id).retry_of == run.id


def test_only_one_of_many_simultaneous_enqueues_wins(uow):
    shop = source(uow)

    def attempt(_):
        try:
            with uow() as work:
                work.runs.enqueue(shop.id)
            return True
        except SourceBusy:
            return False

    with ThreadPoolExecutor(6) as pool:
        outcomes = list(pool.map(attempt, range(6)))
    assert outcomes.count(True) == 1


def test_claim_takes_the_oldest_queued_run_and_none_when_the_queue_is_empty(uow):
    a, b = source(uow, "a"), source(uow, "b")
    with uow() as work:
        older = work.runs.enqueue(a.id)
        work.runs.enqueue(b.id)
        claimed = work.runs.claim_next("w1")
    assert claimed.id == older.id and claimed.status == "running" and claimed.started_at and claimed.heartbeat_at
    with uow() as work:
        assert work.runs.claim_next("w2").source_id == b.id
        assert work.runs.claim_next("w3") is None


def test_two_claimers_never_get_the_same_run(uow):
    with uow() as work:
        ids = {work.runs.enqueue(work.sources.ensure(f"s{n}").id).id for n in range(6)}

    def claim_all(worker):
        got = []
        while True:
            with uow() as work:
                run = work.runs.claim_next(worker)
            if run is None:
                return got
            got.append(run.id)

    with ThreadPoolExecutor(4) as pool:
        claimed = [run_id for batch in pool.map(claim_all, ["a", "b", "c", "d"]) for run_id in batch]
    assert sorted(claimed) == sorted(ids)


def test_a_heartbeat_stores_progress_and_says_when_to_stop(uow):
    shop = source(uow)
    with uow() as work:
        run = work.runs.start(work.runs.enqueue(shop.id).id, "w")
    with uow() as work:
        assert work.runs.heartbeat(run.id, {"requests_count": 4}) is False
        assert work.runs.get(run.id).stats == {"requests_count": 4}
        work.runs.request_cancel(run.id)
    with uow() as work:
        assert work.runs.heartbeat(run.id, {"requests_count": 5}) is True
        work.runs.finish(run.id, "paused", {"requests_count": 6})
    with uow() as work:
        assert work.runs.heartbeat(run.id, {"requests_count": 99}) is True  # no longer running: stop
        assert work.runs.get(run.id).stats == {"requests_count": 6}  # a late beat cannot overwrite the final stats


def test_stopping_a_queued_run_cancels_it_and_a_running_one_is_flagged(uow):
    a, b = source(uow, "a"), source(uow, "b")
    with uow() as work:
        queued = work.runs.enqueue(a.id)
        running = work.runs.start(work.runs.enqueue(b.id).id, "w")
        cancelled = work.runs.request_cancel(queued.id)
        flagged = work.runs.request_cancel(running.id)
        again = work.runs.request_cancel(running.id)
    assert cancelled.status == "cancelled" and cancelled.finished_at is not None
    assert flagged.status == "running" and flagged.cancel_requested is True and again.cancel_requested is True
    with uow() as work:
        work.runs.finish(running.id, "paused", {})
        assert work.runs.request_cancel(running.id) is None  # finished
        assert work.runs.request_cancel(999_999) is None  # missing


def test_reaping_fails_only_running_runs_whose_heartbeat_has_lapsed(uow):
    a, b, c = source(uow, "a"), source(uow, "b"), source(uow, "c")
    with uow() as work:
        stale = work.runs.start(work.runs.enqueue(a.id).id, "w")
        fresh = work.runs.start(work.runs.enqueue(b.id).id, "w")
        waiting = work.runs.enqueue(c.id)
        work.session.execute(update(RunRow).where(RunRow.id == stale.id).values(heartbeat_at=RunRow.heartbeat_at - timedelta(minutes=5)))
    with uow() as work:
        assert work.runs.reap_stale(timedelta(seconds=60)) == [stale.id]
    with uow() as work:
        reaped = work.runs.get(stale.id)
        assert reaped.status == "failed" and reaped.error == "worker lost" and reaped.finished_at is not None
        assert work.runs.get(fresh.id).status == "running" and work.runs.get(waiting.id).status == "queued"


def test_history_is_newest_first_and_latest_by_source_picks_one_each(uow):
    a, b = source(uow, "a"), source(uow, "b")
    with uow() as work:
        first = work.runs.enqueue(a.id)
        work.runs.finish(work.runs.start(first.id, "w").id, "completed", {})
        second = work.runs.enqueue(a.id)
        other = work.runs.enqueue(b.id)
    with uow() as work:
        assert [r.id for r in work.runs.recent(a.id, 10)] == [second.id, first.id]
        assert [r.id for r in work.runs.recent(a.id, 1)] == [second.id]
        assert {sid: r.id for sid, r in work.runs.latest_by_source().items()} == {a.id: second.id, b.id: other.id}


def test_sources_can_be_listed_and_found_by_id_and_the_fetch_feed_is_incremental(uow, blobs):
    shop = source(uow)
    with uow() as work:
        assert [s.name for s in work.sources.all()] == ["shop"] and work.sources.by_id(shop.id).name == "shop"
        assert work.sources.by_id(999_999) is None
        run = work.runs.start(work.runs.enqueue(shop.id).id, "w")
        ids = [work.fetches.save(run.id, url=f"https://x.test/{n}", final_url=f"https://x.test/{n}", status=200, headers={}, body=b"x" * n, fetcher="t").id for n in (1, 2, 3)]
    with uow() as work:
        feed = work.fetches.since(run.id)
        assert [f.id for f in feed] == ids and feed[1].size == 2 and feed[0].url == "https://x.test/1"
        assert [f.id for f in work.fetches.since(run.id, after_id=ids[0])] == ids[1:]
        assert [f.id for f in work.fetches.since(run.id, limit=2)] == ids[:2]
        assert work.fetches.since(run.id, after_id=ids[-1]) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_pipeline_queue.py`
Expected: FAIL at import (`SourceBusy` cannot be imported).

- [ ] **Step 3: Rewrite `src/scraper/pipeline/repositories/runs.py`**

```python
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
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
        if status not in FINISHED:
            raise ValueError(f"a run finishes as one of {FINISHED}, not {status!r}")
        return self._update(run_id, status=status, finished_at=func.now(), stats=stats, error=error)

    def get(self, run_id: int) -> Run | None:
        row = self.session.get(RunRow, run_id)
        return _run(row) if row else None

    def recent(self, source_id: int, limit: int = 30) -> list[Run]:
        rows = self.session.scalars(select(RunRow).where(RunRow.source_id == source_id).order_by(RunRow.id.desc()).limit(limit))
        return [_run(row) for row in rows]

    def latest_by_source(self) -> dict[int, Run]:
        rows = self.session.scalars(select(RunRow).distinct(RunRow.source_id).order_by(RunRow.source_id, RunRow.id.desc()))
        return {row.source_id: _run(row) for row in rows}

    def _update(self, run_id: int, **values) -> Run:
        row = self.session.scalars(
            update(RunRow).where(RunRow.id == run_id).values(**values).returning(RunRow).execution_options(populate_existing=True)
        ).one()
        return _run(row)
```

- [ ] **Step 4: Add the source and fetch queries.** In `repositories/sources.py` append to `SourceRepository`:

```python
    def all(self) -> list[SourceConfig]:
        return [_source(row) for row in self.session.scalars(select(SourceRow).order_by(SourceRow.name))]

    def by_id(self, source_id: int) -> SourceConfig | None:
        row = self.session.get(SourceRow, source_id)
        return _source(row) if row else None
```

In `repositories/fetches.py` change `from ..domain import FetchRef` to `from ..domain import FetchRef, FetchSummary` and append to `FetchRepository`:

```python
    def since(self, run_id: int, after_id: int = 0, limit: int = 200) -> list[FetchSummary]:
        """The run's archived responses with an id above `after_id`, oldest first. The spiders page asks again with the last id it saw."""
        rows = self.session.execute(
            select(FetchRow.id, FetchRow.url, FetchRow.final_url, FetchRow.status, FetchRow.fetched_at, FetchRow.duration_ms, FetchRow.size)
            .where(FetchRow.run_id == run_id, FetchRow.id > after_id)
            .order_by(FetchRow.id)
            .limit(limit)
        )
        return [FetchSummary(*row) for row in rows]
```

In `repositories/__init__.py`:

```python
from .fetches import FetchRepository
from .records import RecordRepository
from .runs import RunRepository, SourceBusy
from .sources import SourceRepository

__all__ = ["FetchRepository", "RecordRepository", "RunRepository", "SourceBusy", "SourceRepository"]
```

- [ ] **Step 5: Run the new and existing pipeline tests**

Run: `uv run pytest -q tests/test_pipeline_queue.py tests/test_pipeline_repositories.py tests/test_pipeline_run.py`
Expected: PASS. If `test_reaping...` fails on the interval expression, the failure message names it; `func.now() - older_than` is the intended form and must work with psycopg's timedelta adaptation.

- [ ] **Step 6: Commit**

```bash
git add src/scraper/pipeline/repositories tests/test_pipeline_queue.py
git commit -m "Add the run queue to the repositories: enqueue, claim, heartbeat, stop, reap, history and the fetch feed"
```

---

### Task 3: Trial mode, `stop_once`, `snapshot` and safe stats in the spider

**Files:**
- Modify: `src/scraper/pipeline/spider.py`
- Modify: `src/scraper/pipeline/runner.py` (carry `kind` and `options`; map a capped trial to `completed`)
- Test: `tests/test_pipeline_trial.py` (new)

**Interfaces:**
- Consumes: Task 2's `enqueue(..., kind, options)`.
- Produces:
  - `RunContext(engine, blobs, source_id, run_id, kind="live", options={})`
  - `SourceSpider.stop_once() -> bool` (True once a pause has been passed to Scrapling; False if no crawl is active yet)
  - `SourceSpider.snapshot() -> dict` (safe from another thread; `{}` before or after a crawl)
  - `SourceSpider.stopped_at_cap: bool`
  - `pipeline_stats()` gains `trial_items` and `stopped_at_cap` for trials
  - `run_source(spider_cls, engine, blobs, *, worker_id="cli", kind="live", options=None) -> Run`
  - `execute(spider_cls, engine, blobs, source_id, run) -> Run` (Task 4 adds the heartbeat)

- [ ] **Step 1: Write the failing tests** `tests/test_pipeline_trial.py`

```python
"""A trial run: the real spider, a few pages, and nothing stored but the evidence."""

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from pipeline_support import book, local_books, serve, write_site
from scraper.pipeline.models import FetchRow, RecordRow, RecordVersionRow
from scraper.pipeline.runner import run_source
from scraper.pipeline.sources.books_toscrape import Book
from scraper.pipeline.spider import RunContext
from scraper.pipeline.uow import UnitOfWork


def counts(pipeline, blobs):
    with UnitOfWork(pipeline, blobs) as uow:
        count = lambda model: uow.session.scalar(select(func.count()).select_from(model))
        return {"records": count(RecordRow), "versions": count(RecordVersionRow), "fetches": count(FetchRow)}


@pytest.fixture
def site(tmp_path):
    write_site(tmp_path / "site", [[book(1), book(2), book(3)], [book(4), book(5)]])
    with serve(tmp_path / "site") as base:
        yield base


def test_a_trial_stops_at_its_cap_keeps_what_it_parsed_and_stores_no_records(pipeline, blobs, site):
    run = run_source(local_books(site), pipeline, blobs, kind="trial", options={"max_requests": 1})
    assert run.kind == "trial" and run.status == "completed" and run.error is None
    assert run.stats["stopped_at_cap"] is True and run.stats["requests_count"] == 1
    assert [item["title"] for item in run.stats["trial_items"]] == ["Book 1", "Book 2", "Book 3"]
    assert counts(pipeline, blobs) == {"records": 0, "versions": 0, "fetches": 1}  # the response is still archived


def test_a_trial_without_a_cap_option_stops_after_five_requests(pipeline, blobs, tmp_path):
    write_site(tmp_path / "long", [[book(n)] for n in range(1, 8)])
    with serve(tmp_path / "long") as base:
        run = run_source(local_books(base), pipeline, blobs, kind="trial")
    assert run.stats["requests_count"] == 5 and counts(pipeline, blobs)["fetches"] == 5


def test_a_trial_keeps_at_most_25_items_and_clips_long_values(pipeline, blobs, tmp_path):
    big = [book(n) for n in range(1, 31)]
    big[0] = {**book(1), "title": "x" * 2000}
    write_site(tmp_path / "big", [big])
    with serve(tmp_path / "big") as base:
        run = run_source(local_books(base), pipeline, blobs, kind="trial", options={"max_requests": 5})
    items = run.stats["trial_items"]
    assert len(items) == 25 and run.stats["items_scraped"] == 30
    assert len(items[0]["title"]) <= 501 and items[0]["title"].endswith("…")


def test_a_trial_counts_and_samples_rejected_items_like_a_live_run(pipeline, blobs, tmp_path):
    write_site(tmp_path / "bad", [[book(1), book(2, price="call us")]])
    with serve(tmp_path / "bad") as base:
        run = run_source(local_books(base), pipeline, blobs, kind="trial")
    assert run.stats["items_rejected"] == 1 and run.stats["rejected_by_field"] == {"price": 1}
    assert [item["title"] for item in run.stats["trial_items"]] == ["Book 1"]


def test_a_live_run_has_no_trial_keys_and_still_stores_records(pipeline, blobs, site):
    run = run_source(local_books(site), pipeline, blobs)
    assert "trial_items" not in run.stats and run.stats["records_created"] == 5 and run.status == "completed"


def test_a_rejected_sample_never_carries_a_nul_character(pipeline, blobs):
    spider = local_books("http://x")(ctx=RunContext(pipeline, blobs, 1, 1))
    item = {"title": "a\x00b", "url": "u", "price": "call\x00us", "rating": "Three", "in_stock": True}
    with pytest.raises(ValidationError) as caught:
        Book.model_validate(item)
    spider._reject(item, caught.value)
    sample = spider.pipeline_stats()["rejected_samples"][0]
    assert all("\x00" not in value for value in sample["item"].values())


def test_a_snapshot_before_a_crawl_is_empty_not_an_error(pipeline, blobs):
    spider = local_books("http://x")(ctx=RunContext(pipeline, blobs, 1, 1))
    assert spider.snapshot() == {}


def test_stop_once_passes_on_only_the_first_request_and_waits_for_a_crawl(pipeline, blobs):
    spider = local_books("http://x")(ctx=RunContext(pipeline, blobs, 1, 1))
    assert spider.stop_once() is False  # no crawl is active, so Scrapling refuses
    calls = []
    spider.pause = lambda: calls.append(1)
    assert spider.stop_once() is True and spider.stop_once() is True
    assert calls == [1]  # a second pause would be a force stop
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_pipeline_trial.py`
Expected: FAIL (`run_source() got an unexpected keyword argument 'kind'`, and `RunContext` takes four arguments).

- [ ] **Step 3: Update `src/scraper/pipeline/spider.py`.** Change the imports and `RunContext`:

```python
import asyncio
import threading
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, field
```

```python
TRIAL_ITEM_LIMIT = 25
TRIAL_DEFAULT_REQUESTS = 5
TRIAL_VALUE_CHARS = 500


@dataclass(frozen=True)
class RunContext:
    engine: Engine
    blobs: FilesystemBlobStore
    source_id: int
    run_id: int
    kind: str = "live"  # "trial" crawls a few pages and stores no records
    options: dict = field(default_factory=dict)


def _clipped(value):
    """A trial's items are shown on a page and kept in `run.stats`, so one huge field must not bloat either."""
    if isinstance(value, str):
        return value if len(value) <= TRIAL_VALUE_CHARS else value[:TRIAL_VALUE_CHARS] + "…"
    if isinstance(value, dict):
        return {key: _clipped(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clipped(item) for item in value]
    return value
```

In `SourceSpider.__init__`, after `self.write_error: str | None = None`, add:

```python
        self.trial = ctx.kind == "trial"
        self.trial_requests = int(ctx.options.get("max_requests", TRIAL_DEFAULT_REQUESTS))
        self.stopped_at_cap = False
        self._trial_items: list[dict] = []
        self._stop_lock = threading.Lock()
        self._stop_asked = False
        self._last_snapshot: dict = {}
```

In `is_blocked`, after `_current_fetch.set(fetch_id)` and before the `return`, add `self._stop_at_trial_cap()`. Add these methods to the class (after `looks_blocked`):

```python
    def _stop_at_trial_cap(self) -> None:
        if self.trial and not self.stopped_at_cap and self.stats.requests_count >= self.trial_requests:
            self.stopped_at_cap = True
            self.stop_once()

    def stop_once(self) -> bool:
        """Ask the crawl to finish what is in flight and stop. Scrapling treats a second request as a force stop, so only the
        first is passed on. False when no crawl is active yet (Scrapling refuses), so the caller may try again."""
        with self._stop_lock:
            if self._stop_asked:
                return True
            try:
                self.pause()
            except RuntimeError:
                return False
            self._stop_asked = True
            return True

    def snapshot(self) -> dict:
        """The numbers so far, for a heartbeat thread. A counter may change while it is copied; then the last good copy stands."""
        try:
            merged = {**self.stats.to_dict(), **self.pipeline_stats()}
        except RuntimeError:  # no crawl is active, or a counter changed mid-copy
            return self._last_snapshot
        self._last_snapshot = merged
        return merged
```

In `on_scraped_item`, replace `self._buffer.append(record)` and the lines after it up to the final `return item` with:

```python
        if self.trial:
            if len(self._trial_items) < TRIAL_ITEM_LIMIT:
                self._trial_items.append(_clipped(record.data))
            return item
        self._buffer.append(record)
        if len(self._buffer) >= BATCH_SIZE:
            await self._flush()
        return item
```

In `_reject`, change the sample line to strip NULs:

```python
            sample = {key: str(value)[:200].replace("\x00", "") for key, value in item.items()}
```

In `_fail`, replace `self.pause()` with `self.stop_once()`. Replace `pipeline_stats`:

```python
    def pipeline_stats(self) -> dict:
        stats = {
            "records_created": self._written.created,
            "records_changed": self._written.changed,
            "records_unchanged": self._written.unchanged,
            "items_rejected": self._rejected,
            "items_unarchived": self._unarchived,
            "rejected_by_field": dict(self._rejected_fields),
            "rejected_samples": list(self._rejected_samples),
        }
        if self.trial:
            stats["trial_items"] = list(self._trial_items)
            stats["stopped_at_cap"] = self.stopped_at_cap
        return stats
```

- [ ] **Step 4: Rewrite `src/scraper/pipeline/runner.py`** (the heartbeat arrives in Task 4):

```python
"""Run one source once: record the run, crawl, store the outcome."""

from sqlalchemy import Engine

from .blobs import FilesystemBlobStore
from .domain import Run
from .spider import RunContext, SourceSpider
from .uow import UnitOfWork


def run_source(
    spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, *, worker_id: str = "cli", kind: str = "live", options: dict | None = None
) -> Run:
    """Blocks until the crawl ends. The run row says how it ended: `completed`, `paused` (stopped; nothing is lost, the records so
    far are saved) or `failed` (storage broke, or the crawl itself raised). Raises `SourceBusy` if the source already has a run in flight."""
    with UnitOfWork(engine, blobs) as uow:
        source = uow.sources.ensure(spider_cls.name)
        run = uow.runs.start(uow.runs.enqueue(source.id, kind=kind, options=options).id, worker_id)
    return execute(spider_cls, engine, blobs, source.id, run)


def execute(spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, source_id: int, run: Run) -> Run:
    """Crawl for a run that is already `running` and finish it."""
    spider = spider_cls(ctx=RunContext(engine, blobs, source_id, run.id, run.kind, run.options))
    try:
        result = spider.start()
    except Exception as error:
        status, stats, message = "failed", spider.pipeline_stats(), f"crawl raised: {error!r}"
    else:
        stats = {**result.stats.to_dict(), **spider.pipeline_stats()}
        status = _status(spider, result.paused)
        message = spider.write_error
    with UnitOfWork(engine, blobs) as uow:
        return uow.runs.finish(run.id, status, stats, message)


def _status(spider: SourceSpider, paused: bool) -> str:
    if spider.write_error:
        return "failed"
    if spider.stopped_at_cap:
        return "completed"  # a trial that reached its cap did what it was asked
    return "paused" if paused else "completed"
```

- [ ] **Step 5: Run the trial tests and the existing pipeline suite**

Run: `uv run pytest -q tests/test_pipeline_trial.py tests/test_pipeline_run.py tests/test_pipeline_items.py`
Expected: PASS. If `test_a_trial_stops_at_its_cap...` fetches 2 pages, `requests_count` is incremented after `is_blocked`'s caller in this Scrapling version; re-read `CrawlerEngine._process_request` (`increment_requests_count` runs before `is_blocked`, as of the installed version) and fix the comparison, not the test.

- [ ] **Step 6: Commit**

```bash
git add src/scraper/pipeline/spider.py src/scraper/pipeline/runner.py tests/test_pipeline_trial.py
git commit -m "Add trial runs and a safe stop to the spider; keep NULs and huge values out of run stats"
```

---

### Task 4: Heartbeat and cancel while a crawl runs

**Files:**
- Modify: `src/scraper/pipeline/runner.py`
- Test: `tests/test_pipeline_execution.py` (new)

**Interfaces:**
- Consumes: `RunRepository.heartbeat`, `SourceSpider.snapshot`, `SourceSpider.stop_once`.
- Produces: `run_source(..., beat_seconds=BEAT_SECONDS)` and `execute(..., *, beat_seconds=BEAT_SECONDS)`; `BEAT_SECONDS = 2.0`. While a crawl runs, a daemon thread writes the spider's snapshot into `run.stats` every `beat_seconds` and stops the crawl (once) when the heartbeat answers "stop".

- [ ] **Step 1: Write the failing tests** `tests/test_pipeline_execution.py`

```python
"""While a crawl runs it reports progress, and a stop request ends it as paused."""

import time
from threading import Thread

from pipeline_support import book, local_books, serve, write_site
from scraper.pipeline.runner import run_source
from scraper.pipeline.uow import UnitOfWork


def wait_for(find, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = find()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError("the condition was not met in time")


def test_a_running_crawl_reports_progress_and_a_stop_request_ends_it_paused(pipeline, blobs, tmp_path):
    write_site(tmp_path / "slow", [[book(n)] for n in range(1, 31)])
    seen = {}

    def latest_with_progress():
        with UnitOfWork(pipeline, blobs) as uow:
            source = uow.sources.get("books_toscrape")
            runs = uow.runs.recent(source.id, 1) if source else []
            return runs[0] if runs and runs[0].stats.get("requests_count", 0) >= 2 else None

    def stopper():
        run = wait_for(latest_with_progress)
        seen["mid"] = run
        with UnitOfWork(pipeline, blobs) as uow:
            uow.runs.request_cancel(run.id)

    with serve(tmp_path / "slow", delay=0.1) as base:
        thread = Thread(target=stopper)
        thread.start()
        finished = run_source(local_books(base), pipeline, blobs, beat_seconds=0.1)
        thread.join()

    assert seen["mid"].status == "running" and seen["mid"].heartbeat_at is not None
    assert finished.status == "paused" and finished.error is None and finished.cancel_requested is True
    assert finished.stats["requests_count"] < 30  # it stopped early
    assert finished.stats["records_created"] >= 1  # and what it had was saved


def test_a_crawl_that_is_not_stopped_completes_and_its_final_stats_win(pipeline, blobs, tmp_path):
    write_site(tmp_path / "short", [[book(1), book(2)], [book(3)]])
    with serve(tmp_path / "short") as base:
        run = run_source(local_books(base), pipeline, blobs, beat_seconds=0.05)
    assert run.status == "completed" and run.stats["records_created"] == 3 and run.cancel_requested is False


def test_a_spider_that_cannot_be_built_fails_the_run_instead_of_leaving_it_running(pipeline, blobs, tmp_path):
    class Broken(local_books("http://x")):
        def __init__(self, **kwargs):
            raise RuntimeError("no sessions")

    run = run_source(Broken, pipeline, blobs)
    assert run.status == "failed" and "could not start the spider" in run.error

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_pipeline_execution.py`
Expected: FAIL (`run_source() got an unexpected keyword argument 'beat_seconds'`).

- [ ] **Step 3: Replace `src/scraper/pipeline/runner.py`** with the heartbeat version:

```python
"""Run one source once: record the run, crawl while reporting progress, store the outcome."""

import threading

from sqlalchemy import Engine

from .blobs import FilesystemBlobStore
from .domain import Run
from .spider import RunContext, SourceSpider
from .uow import UnitOfWork

BEAT_SECONDS = 2.0


def run_source(
    spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, *,
    worker_id: str = "cli", kind: str = "live", options: dict | None = None, beat_seconds: float = BEAT_SECONDS,
) -> Run:
    """Blocks until the crawl ends. The run row says how it ended: `completed`, `paused` (stopped; nothing is lost, the records so
    far are saved) or `failed` (storage broke, or the crawl itself raised). Raises `SourceBusy` if the source already has a run in flight."""
    with UnitOfWork(engine, blobs) as uow:
        source = uow.sources.ensure(spider_cls.name)
        run = uow.runs.start(uow.runs.enqueue(source.id, kind=kind, options=options).id, worker_id)
    return execute(spider_cls, engine, blobs, source.id, run, beat_seconds=beat_seconds)


def execute(
    spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, source_id: int, run: Run, *, beat_seconds: float = BEAT_SECONDS
) -> Run:
    """Crawl for a run that is already `running`, reporting progress as it goes, and finish it. The command line and the worker's
    child process both come through here, so both heartbeat and both can be stopped from the page."""
    try:
        spider = spider_cls(ctx=RunContext(engine, blobs, source_id, run.id, run.kind, run.options))
    except Exception as error:
        with UnitOfWork(engine, blobs) as uow:
            return uow.runs.finish(run.id, "failed", {}, f"could not start the spider: {error!r}")
    stop = threading.Event()
    beat = threading.Thread(target=_beat, args=(stop, spider, engine, blobs, run.id, beat_seconds), name=f"heartbeat-{run.id}", daemon=True)
    beat.start()
    try:
        result = spider.start()
    except Exception as error:
        status, stats, message = "failed", spider.pipeline_stats(), f"crawl raised: {error!r}"
    else:
        stats = {**result.stats.to_dict(), **spider.pipeline_stats()}
        status = _status(spider, result.paused)
        message = spider.write_error
    finally:
        stop.set()
        beat.join(timeout=10)
    with UnitOfWork(engine, blobs) as uow:
        return uow.runs.finish(run.id, status, stats, message)


def _beat(stop: threading.Event, spider: SourceSpider, engine: Engine, blobs: FilesystemBlobStore, run_id: int, every: float) -> None:
    """Write the spider's numbers into the run, and pass a stop request on to it. A beat that fails is skipped: the next one tries
    again, and if the silence lasts the reaper fails the run."""
    while not stop.wait(every):
        try:
            with UnitOfWork(engine, blobs) as uow:
                should_stop = uow.runs.heartbeat(run_id, spider.snapshot())
        except Exception:
            continue
        if should_stop:
            spider.stop_once()


def _status(spider: SourceSpider, paused: bool) -> str:
    if spider.write_error:
        return "failed"
    if spider.stopped_at_cap:
        return "completed"  # a trial that reached its cap did what it was asked
    return "paused" if paused else "completed"
```

- [ ] **Step 4: Run the execution tests and the whole pipeline suite**

Run: `uv run pytest -q tests/test_pipeline_execution.py tests/test_pipeline_trial.py tests/test_pipeline_run.py tests/test_pipeline_queue.py`
Expected: PASS. `test_a_running_crawl...` takes a few seconds (30 pages at 0.1 s, stopped early).

- [ ] **Step 5: Commit**

```bash
git add src/scraper/pipeline/runner.py tests/test_pipeline_execution.py
git commit -m "Heartbeat a running crawl into its run and stop it when the run is flagged"
```

---

### Task 5: The worker, `exec-run` and the CLI commands

**Files:**
- Create: `src/scraper/pipeline/worker.py`
- Modify: `src/scraper/pipeline/__main__.py`
- Modify: `Makefile` (target `pipeline-worker`)
- Test: `tests/test_pipeline_worker.py` (new), `tests/test_pipeline_cli.py` (new)

**Interfaces:**
- Consumes: `execute`, `RunRepository.claim_next/reap_stale/finish/get`.
- Produces:
  - `Worker(engine, blobs, *, concurrency=1, launch=spawn_child, stale_after=timedelta(seconds=60), worker_id=None)` with `.tick() -> int` (runs started), `.serve(stop: threading.Event, poll_seconds=2.0)`, `.shutdown(grace=30.0)`
  - `spawn_child(run_id) -> subprocess.Popen`
  - `exec_run(run_id, engine, blobs, spiders=None) -> Run` (raises `LookupError` for a missing run, `ValueError` if it is not `running`)
  - CLI: `python -m scraper.pipeline worker [--concurrency N]`, `exec-run RUN_ID`; `run` refuses cleanly on `SourceBusy`.

- [ ] **Step 1: Write the failing worker tests** `tests/test_pipeline_worker.py`

```python
"""The worker claims queued runs and watches the process that runs each. The processes here are stand-ins; a real one is
`python -m scraper.pipeline exec-run`."""

import signal
import threading
from datetime import timedelta

import pytest
from sqlalchemy import update

from pipeline_support import book, local_books, serve, write_site
from scraper.pipeline.models import RunRow
from scraper.pipeline.uow import UnitOfWork
from scraper.pipeline.worker import Worker, exec_run


class FakeChild:
    def __init__(self, code=None, on_start=None):
        self.code = code
        self.signals = []
        self.killed = False
        if on_start:
            on_start()

    def poll(self):
        return self.code

    def send_signal(self, number):
        self.signals.append(number)
        self.code = 0

    def kill(self):
        self.killed = True
        self.code = -9


@pytest.fixture
def uow(pipeline, blobs):
    return lambda: UnitOfWork(pipeline, blobs)


def queue(uow, *names):
    with uow() as work:
        return [work.runs.enqueue(work.sources.ensure(name).id) for name in names]


def status_of(uow, run_id):
    with uow() as work:
        return work.runs.get(run_id)


def test_a_tick_claims_the_oldest_queued_run_and_launches_its_process(uow, pipeline, blobs):
    first, _ = queue(uow, "a", "b")
    launched = []
    worker = Worker(pipeline, blobs, launch=lambda run_id: launched.append(run_id) or FakeChild(), worker_id="w1")
    assert worker.tick() == 1 and launched == [first.id]  # concurrency 1: the second waits
    assert status_of(uow, first.id).status == "running"


def test_the_next_run_starts_once_the_first_process_has_gone(uow, pipeline, blobs):
    first, second = queue(uow, "a", "b")
    children = []

    def launch(run_id):
        def finish():
            with uow() as work:
                work.runs.finish(run_id, "completed", {})
        children.append(FakeChild(code=0, on_start=finish))
        return children[-1]

    worker = Worker(pipeline, blobs, launch=launch)
    worker.tick()
    assert status_of(uow, first.id).status == "completed"  # the process finished its run before exiting: nothing to correct
    worker.tick()
    assert status_of(uow, second.id).status == "running"


def test_a_process_that_exits_without_finishing_its_run_fails_it(uow, pipeline, blobs):
    (run,) = queue(uow, "a")
    worker = Worker(pipeline, blobs, launch=lambda run_id: FakeChild(code=3))
    worker.tick()
    worker.tick()
    failed = status_of(uow, run.id)
    assert failed.status == "failed" and "exited with code 3" in failed.error and failed.finished_at is not None


def test_a_process_that_cannot_be_started_fails_its_run(uow, pipeline, blobs):
    (run,) = queue(uow, "a")

    def launch(run_id):
        raise OSError("no such interpreter")

    Worker(pipeline, blobs, launch=launch).tick()
    failed = status_of(uow, run.id)
    assert failed.status == "failed" and "could not start the run process" in failed.error


def test_every_tick_reaps_runs_whose_process_went_quiet(uow, pipeline, blobs):
    (run,) = queue(uow, "a")
    with uow() as work:
        work.runs.start(run.id, "someone-else")
        work.session.execute(update(RunRow).where(RunRow.id == run.id).values(heartbeat_at=RunRow.heartbeat_at - timedelta(minutes=5)))
    Worker(pipeline, blobs, launch=lambda run_id: FakeChild(), stale_after=timedelta(seconds=60)).tick()
    assert status_of(uow, run.id).error == "worker lost"


def test_concurrency_two_runs_two_sources_at_once(uow, pipeline, blobs):
    queue(uow, "a", "b", "c")
    worker = Worker(pipeline, blobs, concurrency=2, launch=lambda run_id: FakeChild())
    assert worker.tick() == 2 and worker.tick() == 0


def test_shutting_down_asks_every_process_to_stop_gracefully(uow, pipeline, blobs):
    queue(uow, "a", "b")
    children = []
    worker = Worker(pipeline, blobs, concurrency=2, launch=lambda run_id: children.append(FakeChild()) or children[-1])
    worker.tick()
    worker.shutdown(grace=1.0)
    assert [child.signals for child in children] == [[signal.SIGINT], [signal.SIGINT]] and not any(child.killed for child in children)


def test_serve_ends_when_told_to_and_survives_a_failing_pass(pipeline, blobs):
    worker = Worker(pipeline, blobs, launch=lambda run_id: FakeChild())
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database blip")
        stop.set()
        return 0

    worker.tick = flaky
    stop = threading.Event()
    worker.serve(stop, poll_seconds=0.01)
    assert len(calls) == 2


def test_exec_run_runs_the_spider_the_run_names(uow, pipeline, blobs, tmp_path):
    write_site(tmp_path / "site", [[book(1), book(2)]])
    with serve(tmp_path / "site") as base:
        (run,) = queue(uow, "books_toscrape")
        with uow() as work:
            work.runs.start(run.id, "w")
        done = exec_run(run.id, pipeline, blobs, {"books_toscrape": local_books(base)})
    assert done.status == "completed" and done.stats["records_created"] == 2


def test_exec_run_refuses_a_run_that_is_not_running_or_does_not_exist(uow, pipeline, blobs):
    (run,) = queue(uow, "books_toscrape")
    with pytest.raises(ValueError):
        exec_run(run.id, pipeline, blobs, {})
    with pytest.raises(LookupError):
        exec_run(999_999, pipeline, blobs, {})
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_pipeline_worker.py`
Expected: FAIL at import (`scraper.pipeline.worker` does not exist).

- [ ] **Step 3: Create `src/scraper/pipeline/worker.py`**

```python
"""The worker: claims queued runs and runs each in a child process. A child, because `Spider.start()` owns its event loop and
installs a SIGINT handler, and because a browser-backed spider needs memory it can give back when it ends."""

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import timedelta
from typing import Callable, Protocol

from sqlalchemy import Engine

from .blobs import FilesystemBlobStore
from .domain import Run
from .runner import execute
from .spider import SourceSpider
from .uow import UnitOfWork

log = logging.getLogger("scraper.pipeline.worker")
STALE_AFTER = timedelta(seconds=60)
POLL_SECONDS = 2.0
GRACE_SECONDS = 30.0


class Child(Protocol):
    def poll(self) -> int | None: ...
    def send_signal(self, number: int) -> None: ...
    def kill(self) -> None: ...


def spawn_child(run_id: int) -> subprocess.Popen:
    """Its own session, so Ctrl+C in the worker's terminal reaches the worker, which then stops its children one by one."""
    return subprocess.Popen([sys.executable, "-m", "scraper.pipeline", "exec-run", str(run_id)], start_new_session=True)


def exec_run(run_id: int, engine: Engine, blobs: FilesystemBlobStore, spiders: dict[str, type[SourceSpider]] | None = None) -> Run:
    """Run a claimed run to its end in this process. This is what the child process does."""
    if spiders is None:
        from .sources import SPIDERS as spiders
    with UnitOfWork(engine, blobs) as uow:
        run = uow.runs.get(run_id)
        source = uow.sources.by_id(run.source_id) if run else None
    if run is None or source is None:
        raise LookupError(f"there is no run {run_id}")
    if run.status != "running":
        raise ValueError(f"run {run_id} is {run.status}, not running")
    return execute(spiders[source.name], engine, blobs, source.id, run)


class Worker:
    def __init__(
        self, engine: Engine, blobs: FilesystemBlobStore, *, concurrency: int = 1, launch: Callable[[int], Child] = spawn_child,
        stale_after: timedelta = STALE_AFTER, worker_id: str | None = None,
    ) -> None:
        self.engine, self.blobs, self.concurrency, self.launch, self.stale_after = engine, blobs, concurrency, launch, stale_after
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.children: dict[int, Child] = {}

    def tick(self) -> int:
        """One pass: note finished processes, fail runs whose process went quiet, then claim runs while there is room. Returns how many started."""
        self._collect()
        with UnitOfWork(self.engine, self.blobs) as uow:
            for run_id in uow.runs.reap_stale(self.stale_after):
                log.warning("run %s went quiet and was failed", run_id)
        started = 0
        while len(self.children) < self.concurrency:
            with UnitOfWork(self.engine, self.blobs) as uow:
                run = uow.runs.claim_next(self.worker_id)
            if run is None:
                break
            try:
                self.children[run.id] = self.launch(run.id)
            except OSError as error:
                with UnitOfWork(self.engine, self.blobs) as uow:
                    uow.runs.finish(run.id, "failed", {}, f"could not start the run process: {error}")
                continue
            started += 1
            log.info("started run %s", run.id)
        return started

    def _collect(self) -> None:
        for run_id, child in list(self.children.items()):
            code = child.poll()
            if code is None:
                continue
            del self.children[run_id]
            with UnitOfWork(self.engine, self.blobs) as uow:
                run = uow.runs.get(run_id)
                if run is not None and run.status == "running":  # the process ended without finishing its run
                    uow.runs.finish(run_id, "failed", run.stats, f"the run process exited with code {code} before it finished")

    def serve(self, stop: threading.Event, poll_seconds: float = POLL_SECONDS) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("a worker pass failed; trying again")  # a database blip must not end the worker
            stop.wait(poll_seconds)
        self.shutdown()

    def shutdown(self, grace: float = GRACE_SECONDS) -> None:
        """Ask each process to finish what is in flight and stop (its run ends `paused`), wait, then kill what is left."""
        for child in self.children.values():
            child.send_signal(signal.SIGINT)
        deadline = time.monotonic() + grace
        while self.children and time.monotonic() < deadline:
            self._collect()
            time.sleep(0.05)
        for child in self.children.values():
            child.kill()
        time.sleep(0.05)
        self._collect()
```

- [ ] **Step 4: Run the worker tests**

Run: `uv run pytest -q tests/test_pipeline_worker.py`
Expected: PASS.

- [ ] **Step 5: Write the failing CLI tests** `tests/test_pipeline_cli.py`

```python
import pytest

from scraper.config import Config
from scraper.pipeline import __main__ as cli


def test_list_names_the_sources(capsys):
    assert cli.main(["list"]) == 0
    assert "books_toscrape" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["worker"], ["exec-run", "1"], ["run", "books_toscrape"]])
def test_commands_that_need_the_database_say_so(monkeypatch, capsys, argv):
    monkeypatch.setattr(cli, "load", lambda: Config())
    assert cli.main(argv) == 2
    assert "SCRAPER_DATABASE_URL" in capsys.readouterr().err


def test_a_worker_needs_a_positive_concurrency():
    with pytest.raises(SystemExit):
        cli.main(["worker", "--concurrency", "0"])
```

- [ ] **Step 6: Replace `src/scraper/pipeline/__main__.py`**

```python
"""`python -m scraper.pipeline run <source>` crawls one source once. `worker` runs queued runs, `exec-run` is what the worker starts for
each (not for typing). `migrate` brings the database up to date. `list` names the sources."""

import argparse
import signal
import sys
import threading

from scraper.config import load

from .blobs import FilesystemBlobStore
from .db import make_engine
from .migrate import current_revision, upgrade
from .repositories import SourceBusy
from .runner import run_source
from .sources import SPIDERS
from .worker import Worker, exec_run


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _summary(finished) -> str:
    stats = finished.stats
    return (
        f"run {finished.id} {finished.status}: {stats.get('requests_count', 0)} requests, "
        f"{stats.get('records_created', 0)} new, {stats.get('records_changed', 0)} changed, "
        f"{stats.get('records_unchanged', 0)} unchanged, {stats.get('items_rejected', 0)} rejected"
        + (f"\n{finished.error}" if finished.error else "")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scraper.pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="name the available sources")
    commands.add_parser("migrate", help="bring the pipeline schema up to date")
    run = commands.add_parser("run", help="crawl one source once (migrates first)")
    run.add_argument("source", choices=sorted(SPIDERS))
    worker = commands.add_parser("worker", help="run queued runs, each in its own process (migrates first)")
    worker.add_argument("--concurrency", type=_positive, default=1, help="how many runs at once, for different sources")
    child = commands.add_parser("exec-run", help="execute one claimed run; the worker starts this, you do not")
    child.add_argument("run_id", type=int)
    args = parser.parse_args(argv)

    if args.command == "list":
        print("\n".join(sorted(SPIDERS)))
        return 0

    cfg = load()
    if not cfg.database_url:
        print("Set SCRAPER_DATABASE_URL (see .env.example).", file=sys.stderr)
        return 2
    engine = make_engine(cfg.database_url)
    blobs = FilesystemBlobStore(cfg.pipeline_blob_dir)
    try:
        upgrade(engine)
        if args.command == "migrate":
            print(f"pipeline schema at revision {current_revision(engine)}")
            return 0
        if args.command == "worker":
            stop = threading.Event()
            for number in (signal.SIGINT, signal.SIGTERM):
                signal.signal(number, lambda *_: stop.set())
            print(f"worker started, running up to {args.concurrency} run(s) at a time; Ctrl+C stops it after pausing its runs")
            Worker(engine, blobs, concurrency=args.concurrency).serve(stop)
            return 0
        if args.command == "exec-run":
            finished = exec_run(args.run_id, engine, blobs, SPIDERS)
            print(_summary(finished))
            return 0 if finished.status in ("completed", "paused") else 1
        try:
            finished = run_source(SPIDERS[args.source], engine, blobs)
        except SourceBusy:
            print("A run is already queued or running for this source.", file=sys.stderr)
            return 2
    finally:
        engine.dispose()
    print(_summary(finished))
    return 0 if finished.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: Add the Makefile target.** In `Makefile` add `pipeline-worker` to the `.PHONY` list and, after `pipeline-run`:

```make
pipeline-worker: ## Run queued spider runs (what the spiders page starts): make pipeline-worker CONCURRENCY=2
	uv run python -m scraper.pipeline worker --concurrency $(or $(CONCURRENCY),1)
```

- [ ] **Step 8: Run the CLI and pipeline tests**

Run: `uv run pytest -q tests/test_pipeline_cli.py tests/test_pipeline_worker.py tests/test_pipeline_run.py tests/test_entrypoint.py`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/scraper/pipeline/worker.py src/scraper/pipeline/__main__.py Makefile tests/test_pipeline_worker.py tests/test_pipeline_cli.py
git commit -m "Add the pipeline worker and the exec-run and worker commands"
```

---

### Task 6: Admin-only `/pipeline` API

**Files:**
- Create: `src/scraper/api/admins.py`, `src/scraper/api/pipeline.py`
- Modify: `src/scraper/api/deps.py`, `src/scraper/api/app.py`
- Modify: `tests/conftest.py` (fixtures `auth_users`, `pipeline_api`), `tests/api_support.py` (Harness takes `pipeline`)
- Test: `tests/test_api_pipeline.py` (new)

**Interfaces:**
- Consumes: Tasks 2 to 5 (`UnitOfWork`, `SourceBusy`, repository methods, `SPIDERS`, `SourceSpider`).
- Produces (all admin-only; errors use `{"error": {"code", "message"}}`):
  - `GET /pipeline/sources` -> `{"sources": [{"name", "latest_run": RunView | null}]}`
  - `GET /pipeline/sources/{name}/runs?limit=` -> `{"runs": [RunView]}` (without `trial_items` and `rejected_samples` in `stats`)
  - `POST /pipeline/sources/{name}/runs` body `{"kind": "live" | "trial", "max_requests"?: 1..20}` -> 201 `RunView`; 409 `source_busy`
  - `GET /pipeline/runs/{id}` -> `RunView` (full stats)
  - `GET /pipeline/runs/{id}/fetches?after=` -> `{"fetches": [{"id","url","final_url","status","fetched_at","duration_ms","size"}]}` (at most 200)
  - `POST /pipeline/runs/{id}/stop` -> 202 `RunView`; 409 `not_active`
  - `POST /pipeline/runs/{id}/retry` -> 201 `RunView`; 409 `not_retryable` or `source_busy`
  - `RunView = {id, source, kind, status, queued_at, started_at, finished_at, heartbeat_at, retry_of, cancel_requested, options, stats, error}` (times are ISO strings or null)
  - `PipelineAccess(engine, blobs, spiders)`; `Deps.pipeline: PipelineAccess | None = None`; `is_admin(pool, user_id) -> bool`; `runnable_spiders() -> dict[str, type[SourceSpider]]`

- [ ] **Step 1: Add the test fixtures.** In `tests/api_support.py` change `Harness.__init__` to accept and keep `pipeline`:

```python
    def __init__(self, pg, url_check=None, pipeline=None, **cfg_overrides):
```
and add `self.pipeline = pipeline` beside `self.pool = pg`. In `build()`, pass `pipeline=self.pipeline` as the last argument of the `Deps(...)` call.

Append to `tests/conftest.py`:

```python
@pytest.fixture
def auth_users(pg_pool):
    """The slice of the web app's `auth.user` table that the API reads, emptied. `add("alice", role="admin")` makes a person."""
    with pg_pool.connection() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS auth")
        conn.execute('CREATE TABLE IF NOT EXISTS auth."user" (id text PRIMARY KEY, role text, banned boolean)')
        conn.execute('TRUNCATE auth."user"')

    def add(user_id, *, role=None, banned=False):
        with pg_pool.connection() as conn:
            conn.execute('INSERT INTO auth."user" (id, role, banned) VALUES (%s, %s, %s)', (user_id, role, banned))

    return add


@pytest.fixture
async def pipeline_api(pg, pipeline, blobs, auth_users):
    """The API with the pipeline wired to the scratch pipeline schema and one spider, `books_toscrape`."""
    from api_support import Harness
    from scraper.api.deps import PipelineAccess
    from scraper.pipeline.sources.books_toscrape import BooksToScrape

    harness = Harness(pg, pipeline=PipelineAccess(pipeline, blobs, {"books_toscrape": BooksToScrape})).build()
    harness.add_user = auth_users
    yield harness
    await harness.close()
```

- [ ] **Step 2: Write the failing tests** `tests/test_api_pipeline.py`

```python
"""The spiders page's routes: administrators only, and every refusal says what is wrong."""

import pytest

from scraper.pipeline.uow import UnitOfWork


@pytest.fixture
async def admin(pipeline_api):
    pipeline_api.add_user("boss", role="admin")
    return pipeline_api, pipeline_api.auth("boss")


def error_code(response):
    return response.json()["error"]["code"]


async def start(api, headers, name="books_toscrape", **body):
    return await api.client.post(f"/pipeline/sources/{name}/runs", headers=headers, json={"kind": "live", **body})


async def test_only_an_active_administrator_gets_in(pipeline_api):
    pipeline_api.add_user("boss", role="admin")
    pipeline_api.add_user("member")
    pipeline_api.add_user("exiled", role="admin", banned=True)
    assert (await pipeline_api.client.get("/pipeline/sources")).status_code == 401
    for user, status in (("boss", 200), ("member", 403), ("exiled", 403), ("stranger", 403)):
        response = await pipeline_api.client.get("/pipeline/sources", headers=pipeline_api.auth(user))
        assert response.status_code == status, user
        if status == 403:
            assert error_code(response) == "forbidden"


async def test_the_spiders_are_listed_with_their_latest_run(admin):
    api, headers = admin
    listed = (await api.client.get("/pipeline/sources", headers=headers)).json()["sources"]
    assert listed == [{"name": "books_toscrape", "latest_run": None}]
    run = (await start(api, headers)).json()
    listed = (await api.client.get("/pipeline/sources", headers=headers)).json()["sources"]
    assert listed[0]["latest_run"]["id"] == run["id"] and listed[0]["latest_run"]["status"] == "queued"


async def test_starting_a_run_queues_it_and_a_second_start_is_refused(admin):
    api, headers = admin
    response = await start(api, headers)
    run = response.json()
    assert response.status_code == 201
    assert (run["source"], run["kind"], run["status"], run["options"], run["retry_of"]) == ("books_toscrape", "live", "queued", {}, None)
    assert run["queued_at"] and run["started_at"] is None and run["heartbeat_at"] is None
    again = await start(api, headers)
    assert again.status_code == 409 and error_code(again) == "source_busy"


async def test_a_trial_carries_its_cap_and_defaults_to_five(admin):
    api, headers = admin
    first = (await start(api, headers, kind="trial", max_requests=3)).json()
    assert first["options"] == {"max_requests": 3}
    await api.client.post(f"/pipeline/runs/{first['id']}/stop", headers=headers)  # frees the source for the next one
    assert (await start(api, headers, kind="trial")).json()["options"] == {"max_requests": 5}


@pytest.mark.parametrize("body", [{"kind": "trial", "max_requests": 0}, {"kind": "trial", "max_requests": 21}, {"kind": "live", "max_requests": 3}, {"kind": "odd"}, {"extra": 1}])
async def test_a_bad_start_request_is_refused_with_422(admin, body):
    api, headers = admin
    response = await api.client.post("/pipeline/sources/books_toscrape/runs", headers=headers, json=body)
    assert response.status_code == 422 and error_code(response) == "invalid"


async def test_an_unknown_spider_or_run_is_a_404(admin):
    api, headers = admin
    for response in (
        await start(api, headers, name="nope"),
        await api.client.get("/pipeline/sources/nope/runs", headers=headers),
        await api.client.get("/pipeline/runs/999999", headers=headers),
        await api.client.get("/pipeline/runs/999999/fetches", headers=headers),
        await api.client.post("/pipeline/runs/999999/stop", headers=headers),
        await api.client.post("/pipeline/runs/999999/retry", headers=headers),
    ):
        assert response.status_code == 404 and error_code(response) == "not_found"
    assert (await api.client.get("/pipeline/runs/1/fetches?after=-1", headers=headers)).status_code == 422


async def test_stopping_a_queued_run_cancels_it_and_a_finished_run_cannot_be_stopped(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers)).json()
    stopped = await api.client.post(f"/pipeline/runs/{run['id']}/stop", headers=headers)
    assert stopped.status_code == 202 and stopped.json()["status"] == "cancelled"
    again = await api.client.post(f"/pipeline/runs/{run['id']}/stop", headers=headers)
    assert again.status_code == 409 and error_code(again) == "not_active"


async def test_stopping_a_running_run_flags_it_and_stopping_twice_is_fine(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers)).json()
    with UnitOfWork(pipeline, blobs) as uow:
        uow.runs.start(run["id"], "w")
    for _ in range(2):
        response = await api.client.post(f"/pipeline/runs/{run['id']}/stop", headers=headers)
        assert response.status_code == 202 and response.json()["cancel_requested"] is True and response.json()["status"] == "running"


async def test_only_a_failed_or_stopped_run_can_be_retried_and_the_retry_points_back(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers, kind="trial", max_requests=2)).json()
    refused = await api.client.post(f"/pipeline/runs/{run['id']}/retry", headers=headers)
    assert refused.status_code == 409 and error_code(refused) == "not_retryable"
    with UnitOfWork(pipeline, blobs) as uow:
        uow.runs.finish(uow.runs.start(run["id"], "w").id, "failed", {}, "boom")
    retry = await api.client.post(f"/pipeline/runs/{run['id']}/retry", headers=headers)
    body = retry.json()
    assert retry.status_code == 201 and body["retry_of"] == run["id"] and body["kind"] == "trial" and body["options"] == {"max_requests": 2}
    busy = await api.client.post(f"/pipeline/runs/{run['id']}/retry", headers=headers)
    assert busy.status_code == 409 and error_code(busy) == "source_busy"


async def test_history_and_the_run_view_split_the_heavy_stats(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers, kind="trial")).json()
    stats = {"requests_count": 2, "trial_items": [{"title": "x"}], "rejected_samples": [{"item": {}, "errors": ["e"]}]}
    with UnitOfWork(pipeline, blobs) as uow:
        uow.runs.finish(uow.runs.start(run["id"], "w").id, "completed", stats)
    listed = (await api.client.get("/pipeline/sources/books_toscrape/runs", headers=headers)).json()["runs"]
    assert listed[0]["stats"] == {"requests_count": 2}
    assert (await api.client.get(f"/pipeline/runs/{run['id']}", headers=headers)).json()["stats"] == stats
    assert (await api.client.get("/pipeline/sources/books_toscrape/runs?limit=0", headers=headers)).status_code == 422


async def test_the_fetch_feed_returns_only_what_is_newer_than_the_cursor(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers)).json()
    with UnitOfWork(pipeline, blobs) as uow:
        uow.runs.start(run["id"], "w")
        ids = [uow.fetches.save(run["id"], url=f"https://x.test/{n}", final_url=f"https://x.test/{n}", status=200, headers={}, body=b"b" * n, fetcher="t").id for n in (1, 2)]
    feed = (await api.client.get(f"/pipeline/runs/{run['id']}/fetches", headers=headers)).json()["fetches"]
    assert [f["id"] for f in feed] == ids and feed[0]["url"] == "https://x.test/1" and feed[1]["size"] == 2 and feed[0]["status"] == 200
    later = (await api.client.get(f"/pipeline/runs/{run['id']}/fetches?after={ids[0]}", headers=headers)).json()["fetches"]
    assert [f["id"] for f in later] == [ids[1]]


async def test_a_server_without_the_pipeline_says_so(pg, auth_users):
    from api_support import Harness

    harness = Harness(pg).build()
    try:
        auth_users("boss", role="admin")
        response = await harness.client.get("/pipeline/sources", headers=harness.auth("boss"))
        assert response.status_code == 503 and error_code(response) == "pipeline_unavailable"
    finally:
        await harness.close()


def test_only_real_pipeline_spiders_are_runnable(monkeypatch):
    from scraper.api.pipeline import runnable_spiders
    from scraper.pipeline.sources import SPIDERS

    class Plain:
        name = "plain"

    monkeypatch.setitem(SPIDERS, "plain", Plain)
    assert "plain" not in runnable_spiders() and "books_toscrape" in runnable_spiders()
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest -q tests/test_api_pipeline.py`
Expected: FAIL (`PipelineAccess` cannot be imported).

- [ ] **Step 4: Create `src/scraper/api/admins.py`**

```python
"""Is this person an administrator? The web app owns that fact, in `auth."user"`; the API may read only these three columns."""

import psycopg
from psycopg_pool import ConnectionPool


class AdminCheckUnavailable(Exception):
    """The table cannot be read: the web app's schema is missing, or this database role was not granted the three columns."""


def is_admin(pool: ConnectionPool, user_id: str) -> bool:
    """Checked on every request, so a banned or demoted administrator loses access at once, not when their token expires."""
    try:
        with pool.connection() as conn:
            row = conn.execute('SELECT role, banned FROM auth."user" WHERE id = %s', (user_id,)).fetchone()
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege, psycopg.errors.InvalidSchemaName) as error:
        raise AdminCheckUnavailable from error
    return row is not None and row[0] == "admin" and not row[1]
```

- [ ] **Step 5: Update `src/scraper/api/deps.py`.** Add imports `from sqlalchemy import Engine`, `from scraper.pipeline.blobs import FilesystemBlobStore`, `from scraper.pipeline.spider import SourceSpider`, then above `Deps`:

```python
@dataclass
class PipelineAccess:
    """What the spiders page's routes need: the pipeline's database, its blob store, and the spiders that can run."""

    engine: Engine
    blobs: FilesystemBlobStore
    spiders: dict[str, type[SourceSpider]]
```

and add `pipeline: PipelineAccess | None = None` as the last field of `Deps`.

- [ ] **Step 6: Create `src/scraper/api/pipeline.py`**

```python
"""The spiders page's routes: what the pipeline can run, how its runs are going, and the buttons that start, stop and retry them.
All of them are for administrators. They only read and write `pipeline` rows; a worker process does the crawling."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field, model_validator

from scraper.pipeline.domain import FetchSummary, Run
from scraper.pipeline.repositories import SourceBusy
from scraper.pipeline.spider import SourceSpider
from scraper.pipeline.uow import UnitOfWork

from .admins import AdminCheckUnavailable, is_admin
from .deps import Deps, PipelineAccess, current_user, get_deps
from .errors import ApiError
from .schemas import Body

router = APIRouter(prefix="/pipeline")
HISTORY_DEFAULT, HISTORY_MAX = 30, 100
FETCH_PAGE = 200
TRIAL_DEFAULT, TRIAL_MAX = 5, 20
HEAVY_STATS = ("trial_items", "rejected_samples")  # only the run's own view carries these
RETRYABLE = ("failed", "paused")


def runnable_spiders() -> dict[str, type[SourceSpider]]:
    """The registered spiders that run through the pipeline. (A spider that is not a `SourceSpider` has nowhere to store anything.)"""
    from scraper.pipeline.sources import SPIDERS

    return {name: cls for name, cls in SPIDERS.items() if isinstance(cls, type) and issubclass(cls, SourceSpider)}


def require_admin(user_id: str = Depends(current_user), deps: Deps = Depends(get_deps)) -> str:
    try:
        allowed = is_admin(deps.pool, user_id)
    except AdminCheckUnavailable:
        raise ApiError(503, "admin_unavailable", "Administrator checks are unavailable right now. Try again shortly.") from None
    if not allowed:
        raise ApiError(403, "forbidden", "Only administrators can use this.")
    return user_id


def pipeline_of(deps: Deps = Depends(get_deps)) -> PipelineAccess:
    if deps.pipeline is None:
        raise ApiError(503, "pipeline_unavailable", "The scraping pipeline is not set up on this server.")
    return deps.pipeline


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def run_view(run: Run, source: str, *, detail: bool = False) -> dict:
    stats = run.stats if detail else {key: value for key, value in run.stats.items() if key not in HEAVY_STATS}
    return {
        "id": run.id, "source": source, "kind": run.kind, "status": run.status, "queued_at": _iso(run.queued_at),
        "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at), "heartbeat_at": _iso(run.heartbeat_at),
        "retry_of": run.retry_of, "cancel_requested": run.cancel_requested, "options": run.options, "stats": stats, "error": run.error,
    }


def fetch_view(fetch: FetchSummary) -> dict:
    return {
        "id": fetch.id, "url": fetch.url, "final_url": fetch.final_url, "status": fetch.status,
        "fetched_at": _iso(fetch.fetched_at), "duration_ms": fetch.duration_ms, "size": fetch.size,
    }


def _busy() -> ApiError:
    return ApiError(409, "source_busy", "A run is already queued or running for this spider.")


def _no_run() -> ApiError:
    return ApiError(404, "not_found", "There is no such run.")


def _known(access: PipelineAccess, name: str) -> None:
    if name not in access.spiders:
        raise ApiError(404, "not_found", "There is no such spider.")


class StartBody(Body):
    kind: Literal["live", "trial"] = "live"
    max_requests: int | None = Field(default=None, ge=1, le=TRIAL_MAX)

    @model_validator(mode="after")
    def _only_trials_have_a_cap(self):
        if self.kind == "live" and self.max_requests is not None:
            raise ValueError("max_requests applies to trial runs only")
        return self


@router.get("/sources")
def sources(access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        known = {source.name: source for source in uow.sources.all()}
        for name in access.spiders:
            if name not in known:
                known[name] = uow.sources.ensure(name)
        latest = uow.runs.latest_by_source()
        listed = [
            {"name": name, "latest_run": run_view(latest[known[name].id], name) if known[name].id in latest else None}
            for name in sorted(access.spiders)
        ]
    return {"sources": listed}


@router.get("/sources/{name}/runs")
def history(
    name: str, limit: int = Query(HISTORY_DEFAULT, ge=1, le=HISTORY_MAX), access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)
) -> dict:
    _known(access, name)
    with UnitOfWork(access.engine, access.blobs) as uow:
        source = uow.sources.get(name)
        runs = uow.runs.recent(source.id, limit) if source else []
    return {"runs": [run_view(run, name) for run in runs]}


@router.post("/sources/{name}/runs", status_code=201)
def start(name: str, body: StartBody, access: PipelineAccess = Depends(pipeline_of), user_id: str = Depends(require_admin)) -> dict:
    _known(access, name)
    options = {"max_requests": body.max_requests or TRIAL_DEFAULT} if body.kind == "trial" else {}
    try:
        with UnitOfWork(access.engine, access.blobs) as uow:
            run = uow.runs.enqueue(uow.sources.ensure(name).id, kind=body.kind, requested_by=user_id, options=options)
    except SourceBusy:
        raise _busy() from None
    return run_view(run, name)


@router.get("/runs/{run_id}")
def one_run(run_id: int, access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        run = uow.runs.get(run_id)
        source = uow.sources.by_id(run.source_id) if run else None
    if run is None or source is None:
        raise _no_run()
    return run_view(run, source.name, detail=True)


@router.get("/runs/{run_id}/fetches")
def fetches(run_id: int, after: int = Query(0, ge=0), access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        if uow.runs.get(run_id) is None:
            raise _no_run()
        rows = uow.fetches.since(run_id, after, FETCH_PAGE)
    return {"fetches": [fetch_view(row) for row in rows]}


@router.post("/runs/{run_id}/stop", status_code=202)
def stop(run_id: int, access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        before = uow.runs.get(run_id)
        if before is None:
            raise _no_run()
        run = uow.runs.request_cancel(run_id)
        source = uow.sources.by_id(before.source_id)
    if run is None:
        raise ApiError(409, "not_active", "This run has already finished.")
    return run_view(run, source.name)


@router.post("/runs/{run_id}/retry", status_code=201)
def retry(run_id: int, access: PipelineAccess = Depends(pipeline_of), user_id: str = Depends(require_admin)) -> dict:
    try:
        with UnitOfWork(access.engine, access.blobs) as uow:
            old = uow.runs.get(run_id)
            source = uow.sources.by_id(old.source_id) if old else None
            if old is None or source is None:
                raise _no_run()
            if old.status not in RETRYABLE:
                raise ApiError(409, "not_retryable", "Only a failed or stopped run can be retried.")
            run = uow.runs.enqueue(old.source_id, kind=old.kind, retry_of=old.id, requested_by=user_id, options=old.options)
    except SourceBusy:
        raise _busy() from None
    return run_view(run, source.name)
```

- [ ] **Step 7: Wire it into `src/scraper/api/app.py`.** Add imports:

```python
from scraper.pipeline.blobs import FilesystemBlobStore
from scraper.pipeline.db import make_engine
from scraper.pipeline.migrate import upgrade

from .deps import Deps, PipelineAccess
from .pipeline import router as pipeline_router, runnable_spiders
```
(replace the existing `from .deps import Deps`). In `build_deps`, before the `return`, add:

```python
    engine = make_engine(cfg.database_url)
    stack.callback(engine.dispose)
    upgrade(engine)  # the pipeline's own schema, like `migrate(pool)` for `app`
    pipeline = PipelineAccess(engine, FilesystemBlobStore(cfg.pipeline_blob_dir), runnable_spiders())
```
and pass `pipeline=pipeline` in the `Deps(...)` call. In `create_app`, after `app.include_router(stream_router)` add `app.include_router(pipeline_router)`.

- [ ] **Step 8: Run the API tests and the rest of the Python suite**

Run: `uv run pytest -q tests/test_api_pipeline.py`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS (nothing else changed behaviour; `Deps.pipeline` defaults to `None`).

- [ ] **Step 9: Commit**

```bash
git add src/scraper/api/admins.py src/scraper/api/pipeline.py src/scraper/api/deps.py src/scraper/api/app.py tests/conftest.py tests/api_support.py tests/test_api_pipeline.py
git commit -m "Add the admin-only /pipeline routes for the spiders page"
```

---

### Task 7: Web foundation: types, pure helpers, query hooks, route guard and menu link

**Files:**
- Create: `web/src/lib/spiders/types.ts`, `web/src/lib/spiders/derive.ts`, `web/src/components/spiders/queries.ts`, `web/src/components/spiders/useNow.ts`
- Modify: `web/src/proxy.ts`, `web/tests/proxy.test.ts`, `web/src/components/workspace/AccountMenu.tsx`
- Test: `web/tests/spiders/derive.test.ts` (new)

**Interfaces:**
- Consumes: Task 6's route shapes.
- Produces (all in `lib/spiders/derive.ts` unless noted):
  - `isActive(status)`, `statusLabel(status)`, `canStop(run)`, `isStopping(run)`, `canRetry(run)`
  - `ago(iso, now)`, `duration(totalSeconds)`, `elapsed(run, now) -> string | null`
  - `workerNotice(run, now) -> { text: string } | null`
  - `counters(run) -> { label: string; value: number }[]`
  - `statusTone(httpStatus) -> "ok" | "muted" | "bad"`, `formatBytes(n)`, `endNote(run) -> string | null`
  - Hooks in `components/spiders/queries.ts`: `useSources()`, `useRuns(name)`, `useRun(id)`, `useFetchFeed(runId, active)`, `useRunActions(name?)`; `POLL_RUN_MS = 2000`, `POLL_LIST_MS = 6000`
  - `useNow(live: boolean) -> number` (ms; ticks each second while `live`)

- [ ] **Step 1: Write the failing tests** `web/tests/spiders/derive.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { ago, canRetry, canStop, counters, duration, elapsed, endNote, formatBytes, isActive, isStopping, statusLabel, statusTone, workerNotice } from "../../src/lib/spiders/derive";
import type { Run } from "../../src/lib/spiders/types";

const NOW = Date.parse("2026-10-12T12:00:00Z");
const at = (secondsAgo: number) => new Date(NOW - secondsAgo * 1000).toISOString();

const run = (fields: Partial<Run> = {}): Run => ({
  id: 7, source: "books_toscrape", kind: "live", status: "running", queued_at: at(120), started_at: at(100), finished_at: null, heartbeat_at: at(1),
  retry_of: null, cancel_requested: false, options: {}, stats: {}, error: null, ...fields,
});

describe("status", () => {
  it("knows which runs are in flight, in the words a person uses", () => {
    expect(["queued", "running", "completed", "paused", "failed", "cancelled"].map((s) => isActive(s as Run["status"]))).toEqual([true, true, false, false, false, false]);
    expect(statusLabel("paused")).toBe("Stopped");
    expect(statusLabel("cancelled")).toBe("Cancelled");
  });
  it("allows Stop once, while a run is in flight, and shows Stopping until it ends", () => {
    expect(canStop(run())).toBe(true);
    expect(canStop(run({ cancel_requested: true }))).toBe(false);
    expect(isStopping(run({ cancel_requested: true }))).toBe(true);
    expect(canStop(run({ status: "completed" }))).toBe(false);
    expect(isStopping(run({ status: "queued", cancel_requested: false }))).toBe(false);
  });
  it("allows Retry for a failed or stopped run only", () => {
    expect((["failed", "paused", "completed", "running", "queued", "cancelled"] as const).map((status) => canRetry(run({ status })))).toEqual([true, true, false, false, false, false]);
  });
});

describe("time", () => {
  it.each([[2, "just now"], [40, "40s ago"], [300, "5m ago"], [7300, "2h ago"], [200000, "2d ago"]])("%s seconds ago reads %s", (seconds, text) => {
    expect(ago(at(seconds), NOW)).toBe(text);
  });
  it("never reads a clock that is slightly ahead as negative", () => expect(ago(at(-30), NOW)).toBe("just now"));
  it.each([[42, "42s"], [65, "1m 05s"], [3725, "1h 02m"]])("%s seconds is %s", (seconds, text) => expect(duration(seconds)).toBe(text));
  it("measures a running run to now and a finished one to its end", () => {
    expect(elapsed(run(), NOW)).toBe("1m 40s");
    expect(elapsed(run({ status: "completed", finished_at: at(40) }), NOW)).toBe("1m 00s");
    expect(elapsed(run({ status: "queued", started_at: null }), NOW)).toBeNull();
  });
});

describe("worker notices", () => {
  it("says how to start a worker when a run has waited more than 30 seconds", () => {
    expect(workerNotice(run({ status: "queued", started_at: null, heartbeat_at: null, queued_at: at(31) }), NOW)?.text).toBe("No worker has picked this up. Start one with make pipeline-worker.");
    expect(workerNotice(run({ status: "queued", started_at: null, heartbeat_at: null, queued_at: at(29) }), NOW)).toBeNull();
  });
  it("warns when a running run's worker has been quiet for more than 15 seconds", () => {
    expect(workerNotice(run({ heartbeat_at: at(40) }), NOW)?.text).toBe("Worker last seen 40s ago.");
    expect(workerNotice(run({ heartbeat_at: at(5) }), NOW)).toBeNull();
  });
  it("is silent for a finished run", () => expect(workerNotice(run({ status: "failed", heartbeat_at: at(900) }), NOW)).toBeNull());
});

describe("counters", () => {
  it("shows six totals for a live run, zero when the stats are empty", () => {
    expect(counters(run())).toEqual([
      { label: "Requests", value: 0 }, { label: "New", value: 0 }, { label: "Changed", value: 0 },
      { label: "Unchanged", value: 0 }, { label: "Rejected", value: 0 }, { label: "Blocked", value: 0 },
    ]);
    const stats = { requests_count: 38, records_created: 740, records_changed: 12, records_unchanged: 3, items_rejected: 2, blocked_requests_count: 1 };
    expect(counters(run({ stats })).map((c) => c.value)).toEqual([38, 740, 12, 3, 2, 1]);
  });
  it("shows what a trial parsed instead of what it stored", () => {
    const stats = { requests_count: 5, items_scraped: 98, items_rejected: 2, blocked_requests_count: 0 };
    expect(counters(run({ kind: "trial", stats }))).toEqual([
      { label: "Requests", value: 5 }, { label: "Parsed", value: 98 }, { label: "Rejected", value: 2 }, { label: "Blocked", value: 0 },
    ]);
  });
  it("explains how a trial ended", () => {
    expect(endNote(run({ kind: "trial", status: "completed", options: { max_requests: 5 }, stats: { stopped_at_cap: true } }))).toBe("Stopped at the page cap (5 requests).");
    expect(endNote(run({ status: "completed" }))).toBeNull();
  });
});

describe("the ledger", () => {
  it.each([[200, "ok"], [204, "ok"], [301, "muted"], [404, "bad"], [429, "bad"], [503, "bad"], [0, "bad"]])("status %s is %s", (code, tone) => expect(statusTone(code)).toBe(tone));
  it.each([[0, "0 B"], [900, "900 B"], [2048, "2.0 KB"], [5 * 1024 * 1024, "5.0 MB"]])("%s bytes is %s", (n, text) => expect(formatBytes(n)).toBe(text));
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npm test -- tests/spiders/derive.test.ts`
Expected: FAIL (cannot find `src/lib/spiders/derive`).

- [ ] **Step 3: Create `web/src/lib/spiders/types.ts`**

```ts
/** What the Python API's /pipeline routes send. */

export type RunStatus = "queued" | "running" | "completed" | "paused" | "failed" | "cancelled";
export type RunKind = "live" | "trial";

export type RejectedSample = { item: Record<string, string>; errors: string[] };

export type RunStats = {
  requests_count?: number;
  blocked_requests_count?: number;
  failed_requests_count?: number;
  items_scraped?: number;
  records_created?: number;
  records_changed?: number;
  records_unchanged?: number;
  items_rejected?: number;
  rejected_by_field?: Record<string, number>;
  /** Only on a run's own view (GET /pipeline/runs/{id}), not in lists. */
  rejected_samples?: RejectedSample[];
  trial_items?: Record<string, unknown>[];
  stopped_at_cap?: boolean;
};

export type Run = {
  id: number;
  source: string;
  kind: RunKind;
  status: RunStatus;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  retry_of: number | null;
  cancel_requested: boolean;
  options: { max_requests?: number };
  stats: RunStats;
  error: string | null;
};

export type SourceSummary = { name: string; latest_run: Run | null };

export type FetchRow = { id: number; url: string; final_url: string; status: number; fetched_at: string; duration_ms: number | null; size: number };
```

- [ ] **Step 4: Create `web/src/lib/spiders/derive.ts`**

```ts
/** Everything the spiders page decides from a run, kept apart from the screens so it can be tested without a browser. */
import type { Run, RunStatus } from "./types";

export const NO_WORKER_AFTER_SECONDS = 30;
export const STALE_BEAT_AFTER_SECONDS = 15;

const LABELS: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  paused: "Stopped", // the run was stopped; what it saved is kept
  failed: "Failed",
  cancelled: "Cancelled",
};

export const isActive = (status: RunStatus): boolean => status === "queued" || status === "running";
export const statusLabel = (status: RunStatus): string => LABELS[status];
export const canStop = (run: Run): boolean => isActive(run.status) && !run.cancel_requested;
export const isStopping = (run: Run): boolean => run.status === "running" && run.cancel_requested;
export const canRetry = (run: Run): boolean => run.status === "failed" || run.status === "paused";

const secondsBetween = (iso: string, now: number): number => Math.max(0, Math.round((now - Date.parse(iso)) / 1000));

export function ago(iso: string, now: number): string {
  const seconds = secondsBetween(iso, now);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const two = (n: number): string => String(n).padStart(2, "0");

export function duration(total: number): string {
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m ${two(total % 60)}s`;
  return `${Math.floor(total / 3600)}h ${two(Math.floor((total % 3600) / 60))}m`;
}

export function elapsed(run: Run, now: number): string | null {
  if (!run.started_at) return null;
  const end = run.finished_at ? Date.parse(run.finished_at) : now;
  return duration(Math.max(0, Math.round((end - Date.parse(run.started_at)) / 1000)));
}

export function workerNotice(run: Run, now: number): { text: string } | null {
  if (run.status === "queued" && secondsBetween(run.queued_at, now) > NO_WORKER_AFTER_SECONDS) {
    return { text: "No worker has picked this up. Start one with make pipeline-worker." };
  }
  if (run.status === "running" && run.heartbeat_at && secondsBetween(run.heartbeat_at, now) > STALE_BEAT_AFTER_SECONDS) {
    return { text: `Worker last seen ${ago(run.heartbeat_at, now)}.` };
  }
  return null;
}

export type Counter = { label: string; value: number };

export function counters(run: Run): Counter[] {
  const s = run.stats;
  const n = (value: number | undefined) => value ?? 0;
  if (run.kind === "trial") {
    return [
      { label: "Requests", value: n(s.requests_count) },
      { label: "Parsed", value: n(s.items_scraped) },
      { label: "Rejected", value: n(s.items_rejected) },
      { label: "Blocked", value: n(s.blocked_requests_count) },
    ];
  }
  return [
    { label: "Requests", value: n(s.requests_count) },
    { label: "New", value: n(s.records_created) },
    { label: "Changed", value: n(s.records_changed) },
    { label: "Unchanged", value: n(s.records_unchanged) },
    { label: "Rejected", value: n(s.items_rejected) },
    { label: "Blocked", value: n(s.blocked_requests_count) },
  ];
}

export function endNote(run: Run): string | null {
  if (run.kind === "trial" && run.stats.stopped_at_cap) {
    const cap = run.options.max_requests;
    return cap ? `Stopped at the page cap (${cap} requests).` : "Stopped at the page cap.";
  }
  return null;
}

export function statusTone(status: number): "ok" | "muted" | "bad" {
  if (status >= 200 && status < 300) return "ok";
  if (status >= 300 && status < 400) return "muted";
  return "bad";
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
```

- [ ] **Step 5: Run the unit tests**

Run: `cd web && npm test -- tests/spiders/derive.test.ts`
Expected: PASS.

- [ ] **Step 6: Create the hooks** `web/src/components/spiders/queries.ts`

```ts
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { ApiError } from "../../lib/api/client";
import { isActive } from "../../lib/spiders/derive";
import type { FetchRow, Run, SourceSummary } from "../../lib/spiders/types";
import { useApi } from "../workspace/ApiProvider";

export const POLL_RUN_MS = 2000;
export const POLL_LIST_MS = 6000;
const FETCH_PAGE = 200; // the API's page size: a full page means there may be more

const keys = {
  sources: ["pipeline", "sources"] as const,
  runs: (name: string) => ["pipeline", "runs", name] as const,
  run: (id: number | null) => ["pipeline", "run", id] as const,
  fetches: (id: number | null) => ["pipeline", "fetches", id] as const,
};

export const messageOf = (error: unknown): string => (error instanceof ApiError ? error.message : "Something went wrong. Try again.");

export function useSources() {
  const api = useApi();
  return useQuery({
    queryKey: keys.sources,
    queryFn: async () => (await api.get<{ sources: SourceSummary[] }>("/pipeline/sources")).sources,
    refetchInterval: (query) => (query.state.data?.some((source) => source.latest_run && isActive(source.latest_run.status)) ? POLL_LIST_MS : false),
  });
}

export function useRuns(name: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.runs(name),
    queryFn: async () => (await api.get<{ runs: Run[] }>(`/pipeline/sources/${encodeURIComponent(name)}/runs`)).runs,
    refetchInterval: (query) => (query.state.data?.some((run) => isActive(run.status)) ? POLL_LIST_MS : false),
  });
}

/** One run in full (with its trial items and rejected samples), refreshed every two seconds while it is in flight. */
export function useRun(id: number | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.run(id),
    enabled: id !== null,
    queryFn: () => api.get<Run>(`/pipeline/runs/${id}`),
    refetchInterval: (query) => (query.state.data && isActive(query.state.data.status) ? POLL_RUN_MS : false),
  });
}

/** The run's fetched pages, oldest first. Each poll asks only for what came after the last one it has. */
export function useFetchFeed(runId: number | null, active: boolean) {
  const api = useApi();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keys.fetches(runId),
    enabled: runId !== null,
    queryFn: async () => {
      let known = client.getQueryData<FetchRow[]>(keys.fetches(runId)) ?? [];
      for (let page = 0; page < 10; page++) {
        const after = known.length ? known[known.length - 1].id : 0;
        const { fetches } = await api.get<{ fetches: FetchRow[] }>(`/pipeline/runs/${runId}/fetches?after=${after}`);
        if (fetches.length) known = [...known, ...fetches];
        if (fetches.length < FETCH_PAGE) break;
      }
      return known;
    },
    refetchInterval: active ? POLL_RUN_MS : false,
  });
  const wasActive = useRef(active);
  const { refetch } = query;
  useEffect(() => {
    if (wasActive.current && !active) void refetch(); // the run just ended: fetch the pages that arrived after the last poll
    wasActive.current = active;
  }, [active, refetch]);
  return query;
}

/** Start, stop and retry. Each refreshes the lists, and `onError` gets the API's own sentence (for a toast). */
export function useRunActions(onError: (message: string) => void, onStarted?: (run: Run) => void) {
  const api = useApi();
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: ["pipeline"] });
  const settle = { onSuccess: refresh, onError: (error: unknown) => onError(messageOf(error)) };
  const start = useMutation({
    mutationFn: ({ name, kind, maxRequests }: { name: string; kind: "live" | "trial"; maxRequests?: number }) =>
      api.post<Run>(`/pipeline/sources/${encodeURIComponent(name)}/runs`, { kind, ...(kind === "trial" && maxRequests ? { max_requests: maxRequests } : {}) }),
    onSuccess: (run) => {
      void refresh();
      onStarted?.(run);
    },
    onError: settle.onError,
  });
  const stop = useMutation({ mutationFn: (runId: number) => api.post<Run>(`/pipeline/runs/${runId}/stop`), ...settle });
  const retry = useMutation({
    mutationFn: (runId: number) => api.post<Run>(`/pipeline/runs/${runId}/retry`),
    onSuccess: (run) => {
      void refresh();
      onStarted?.(run);
    },
    onError: settle.onError,
  });
  return { start, stop, retry };
}
```

- [ ] **Step 7: Create `web/src/components/spiders/useNow.ts`**

```ts
"use client";

import { useEffect, useState } from "react";

/** The current time in milliseconds, refreshed every second while `live`, so "40s ago" and the elapsed time keep moving. */
export function useNow(live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);
  return now;
}
```

- [ ] **Step 8: Guard the route and link to it.** In `web/src/proxy.ts` change the matcher:

```ts
export const config = { matcher: ["/", "/admin/:path*", "/spiders/:path*"] };
```

In `web/tests/proxy.test.ts` change the loop paths to `["/", "/admin", "/spiders", "/spiders/books_toscrape"]` and the matcher expectation to `["/", "/admin/:path*", "/spiders/:path*"]`.

In `web/src/components/workspace/AccountMenu.tsx`, inside the `person.isAdmin` block, add a second link before the existing one:

```tsx
          {person.isAdmin ? (
            <>
              <Link className="item" href="/spiders">
                Spiders
              </Link>
              <Link className="item" href="/admin">
                Invitations and people
              </Link>
            </>
          ) : null}
```

- [ ] **Step 9: Typecheck and run the web unit tests**

Run: `cd web && npm run typecheck && npm test -- tests/spiders tests/proxy.test.ts`
Expected: PASS. (`/spiders` has no page yet; the proxy test only checks redirects.)

- [ ] **Step 10: Commit**

```bash
git add web/src/lib/spiders web/src/components/spiders/queries.ts web/src/components/spiders/useNow.ts web/src/proxy.ts web/src/components/workspace/AccountMenu.tsx web/tests/spiders web/tests/proxy.test.ts
git commit -m "Add the spiders page's types, helpers, query hooks, route guard and menu link"
```

---

### Task 8: The spiders page: components, styles and routes

**Files:**
- Create: `web/src/components/spiders/{SpidersFrame,StatusPill,SpiderTable,SpiderPage,RunList,RunHeader,RunLedger,TrialResults}.tsx`, `web/src/components/spiders/spiders.css`
- Create: `web/src/app/spiders/page.tsx`, `web/src/app/spiders/[name]/page.tsx`

**Interfaces:**
- Consumes: Task 7's helpers, hooks and types; `useToast`/`Toast`, `WorkspaceProviders`, `requireAdmin`, `getServices`, `hostAndPath` (`lib/api/format.ts`), `Mark`.
- Produces: routes `/spiders` and `/spiders/[name]`. Visible text the e2e spec relies on: heading "Spiders"; a table row per spider with buttons named "Run now", "Trial run", "Stop", "Retry"; the status pill words from `statusLabel`.

Design notes (from the spec; no new palette): the page reuses the app's tokens. The numbered, ruled **pages fetched** ledger is the one distinctive element; counters are one ruled totals strip, not cards. The only motion is the pulsing dot on a running pill (off under `prefers-reduced-motion`). There is no component test harness in `web/` (Vitest runs in `node`); behaviour lives in `derive.ts` (tested) and the screens are covered by the Playwright spec in Task 9 and the visual check there.

- [ ] **Step 1: Read the installed Next.js docs for the two conventions this task uses**

Run: `sed -n 1,70p web/node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/dynamic-routes.md` and `grep -n "generateMetadata" -A12 web/node_modules/next/dist/docs/01-app/03-api-reference/04-functions/generate-metadata.md | head -40`
Expected: `params` is a `Promise` and `generateMetadata` receives `{ params }` the same way. If the installed docs say otherwise, follow them in Step 9 and Step 10.

- [ ] **Step 2: Create the styles** `web/src/components/spiders/spiders.css`

```css
/* The spiders page. It uses the app's tokens (globals.css); `.sp-` marks what is local to this page. */
.spiders { flex: 1; padding: 2.25rem 1.25rem 3rem; }
.spiders-inner { width: min(100%, 72rem); margin: 0 auto; display: grid; gap: 1.5rem; }
.spiders h1 { font-family: var(--font-read), Georgia, serif; font-size: var(--step-4); font-weight: 600; line-height: 1.1; letter-spacing: -0.02em; }
.spiders h2, .spiders h3 { font-family: var(--font-read), Georgia, serif; font-weight: 600; letter-spacing: -0.01em; }
.spiders h2 { font-size: var(--step-2); }
.spiders h3 { font-size: var(--step-1); margin-bottom: 0.5rem; }
.sp-lede { color: var(--muted); max-width: 60ch; margin-top: 0.4rem; }
.sp-head { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 1rem; }
.sp-back { font-size: var(--step--1); }
.sp-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; }
.sp-controls label { display: flex; align-items: center; gap: 0.4rem; font-size: var(--step--1); font-weight: 600; }
.sp-controls select { padding-block: 0.3rem; }

.sp-pill { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.1rem 0.65rem; border-radius: 999px; font-size: var(--step--1); font-weight: 600; white-space: nowrap; }
.sp-pill::before { content: ""; width: 0.4rem; height: 0.4rem; border-radius: 50%; background: currentColor; }
.sp-pill.queued { background: var(--warn-soft); color: var(--warn); }
.sp-pill.running { background: var(--accent-soft); color: var(--accent); }
.sp-pill.running::before { animation: sp-pulse 1.4s ease-in-out infinite; }
.sp-pill.completed { background: var(--ok-soft); color: var(--ok); }
.sp-pill.paused, .sp-pill.cancelled { background: var(--sunken); color: var(--muted); }
.sp-pill.failed { background: var(--bad-soft); color: var(--bad); }
.sp-badge { margin-left: 0.35rem; padding: 0.05rem 0.55rem; border: 1px solid var(--rule); border-radius: 999px; font-size: var(--step--1); color: var(--muted); white-space: nowrap; }
@keyframes sp-pulse { 50% { opacity: 0.25; } }

.sp-table-wrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: var(--radius-l); background: var(--sheet); box-shadow: var(--shadow-s); }
.sp-table { width: 100%; border-collapse: collapse; }
.sp-table th { text-align: left; font-size: var(--step--1); color: var(--muted); font-weight: 650; padding: 0.65rem 1rem; border-bottom: 2px solid var(--ink); white-space: nowrap; }
.sp-table td { padding: 0.7rem 1rem; border-bottom: 1px solid var(--ledger-line); vertical-align: middle; }
.sp-table tr:last-child td { border-bottom: 0; }
.sp-table tbody tr:hover td { background: var(--paper); }
.sp-table .name { font-family: var(--font-read), Georgia, serif; font-weight: 600; font-size: var(--step-1); }
.sp-table .act { text-align: right; white-space: nowrap; }
.sp-table .act .btn + .btn { margin-left: 0.4rem; }

.sp-grid { display: grid; grid-template-columns: 15rem minmax(0, 1fr); gap: 1.5rem; align-items: start; }
.runlist { list-style: none; margin: 0; padding: 0; border: 1px solid var(--rule); border-radius: var(--radius-l); background: var(--sheet); overflow: hidden; }
.runlist button { display: flex; flex-wrap: wrap; align-items: center; gap: 0.2rem 0.5rem; width: 100%; padding: 0.6rem 0.8rem; border: 0; border-bottom: 1px solid var(--ledger-line); background: transparent; text-align: left; }
.runlist li:last-child button { border-bottom: 0; }
.runlist button:hover { background: var(--paper); }
.runlist button[aria-current="true"] { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
.runlist .when { flex-basis: 100%; color: var(--muted); font-size: var(--step--1); }
.runpick { display: none; width: 100%; }
.sp-main { display: grid; gap: 1.25rem; min-width: 0; }

.run-head { display: grid; gap: 0.7rem; }
.run-title { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; }
.run-title .run-actions { margin-left: auto; display: flex; gap: 0.4rem; }
.sp-notice { background: var(--warn-soft); color: var(--warn); border-radius: var(--radius); padding: 0.6rem 0.9rem; }
.totals { display: flex; flex-wrap: wrap; margin: 0; border-top: 2px solid var(--ink); border-bottom: 1px solid var(--rule); }
.totals > div { flex: 1 1 7rem; padding: 0.6rem 0.9rem; border-right: 1px solid var(--ledger-line); }
.totals > div:last-child { border-right: 0; }
.totals dt { color: var(--muted); font-size: var(--step--1); }
.totals dd { margin: 0; font-family: var(--font-read), Georgia, serif; font-size: var(--step-2); font-weight: 600; font-variant-numeric: tabular-nums; }

.sp-ledger { width: 100%; border-collapse: collapse; font-size: var(--step--1); font-variant-numeric: tabular-nums; }
.sp-ledger th { text-align: left; color: var(--muted); font-weight: 650; padding: 0.5rem 0.8rem; border-bottom: 2px solid var(--ink); white-space: nowrap; }
.sp-ledger td { padding: 0.4rem 0.8rem; border-bottom: 1px solid var(--ledger-line); }
.sp-ledger tr:last-child td { border-bottom: 0; }
.sp-ledger .n { width: 3.2rem; text-align: right; color: var(--muted); background: var(--accent-soft); border-right: 3px double var(--accent); }
.sp-ledger .num { text-align: right; white-space: nowrap; }
.sp-ledger .addr { width: 100%; max-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sp-code { font-weight: 650; }
.sp-code.ok { color: var(--ok); }
.sp-code.muted { color: var(--muted); }
.sp-code.bad { color: var(--bad); }

.sp-tabs { display: flex; gap: 0.25rem; border-bottom: 1px solid var(--rule); margin-bottom: 0.9rem; }
.sp-tabs button { background: none; border: 0; border-bottom: 2px solid transparent; padding: 0.5rem 0.8rem; color: var(--muted); font-weight: 600; }
.sp-tabs button[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--accent); }
.sp-rejects { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.7rem; }
.sp-rejects li { border: 1px solid var(--rule); border-radius: var(--radius); background: var(--sheet); padding: 0.7rem 0.9rem; display: grid; gap: 0.35rem; }
.sp-rejects .why { color: var(--bad); font-size: var(--step--1); }
.sp-rejects .fields { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 0.1rem 0.8rem; margin: 0; font-size: var(--step--1); }
.sp-rejects .fields dt { color: var(--muted); }
.sp-rejects .fields dd { margin: 0; overflow-wrap: anywhere; }
.sp-empty { padding: 1.4rem 1.1rem; border: 1px dashed var(--rule); border-radius: var(--radius-l); color: var(--muted); }

@media (max-width: 56rem) {
  .sp-grid { grid-template-columns: minmax(0, 1fr); }
  .runlist { display: none; }
  .runpick { display: block; }
}
@media (prefers-reduced-motion: reduce) {
  .sp-pill.running::before { animation: none; }
}
```

- [ ] **Step 3: Create the shared pieces.** `web/src/components/spiders/SpidersFrame.tsx` (server component):

```tsx
import Link from "next/link";
import type { ReactNode } from "react";
import { Mark } from "../Mark";
import "./spiders.css";

export function SpidersFrame({ children }: { children: ReactNode }) {
  return (
    <div className="page">
      <header className="topbar">
        <Link className="wordmark" href="/">
          <Mark />
          Page Chat
        </Link>
        <Link className="btn btn-quiet" href="/">
          Back to workspace
        </Link>
      </header>
      <main className="spiders">
        <div className="spiders-inner">{children}</div>
      </main>
    </div>
  );
}
```

`web/src/components/spiders/StatusPill.tsx`:

```tsx
import { statusLabel } from "../../lib/spiders/derive";
import type { Run } from "../../lib/spiders/types";

export function StatusPill({ run }: { run: Pick<Run, "status" | "kind"> }) {
  return (
    <>
      <span className={`sp-pill ${run.status}`}>{statusLabel(run.status)}</span>
      {run.kind === "trial" ? <span className="sp-badge">Trial</span> : null}
    </>
  );
}
```

- [ ] **Step 4: Create the list** `web/src/components/spiders/SpiderTable.tsx`

```tsx
"use client";

import Link from "next/link";
import { ago, canRetry, canStop, isActive, isStopping } from "../../lib/spiders/derive";
import type { Run } from "../../lib/spiders/types";
import { Toast, useToast } from "../Toast";
import { messageOf, useRunActions, useSources } from "./queries";
import { StatusPill } from "./StatusPill";
import { useNow } from "./useNow";

function recordsNote(run: Run): string {
  const s = run.stats;
  if (run.kind === "trial") return `${s.items_scraped ?? 0} parsed`;
  return `${s.records_created ?? 0} new, ${s.records_changed ?? 0} changed`;
}

export function SpiderTable() {
  const sources = useSources();
  const [toast, setToast] = useToast();
  const { start, stop, retry } = useRunActions(setToast);
  const anyActive = sources.data?.some((source) => source.latest_run && isActive(source.latest_run.status)) ?? false;
  const now = useNow(anyActive);
  const busy = start.isPending || stop.isPending || retry.isPending;

  return (
    <>
      <div>
        <h1>Spiders</h1>
        <p className="sp-lede">Start a crawl, watch it as it runs, and check a spider against its site with a trial run before you trust it.</p>
      </div>
      {sources.isPending ? (
        <p role="status" className="muted">
          Loading spiders…
        </p>
      ) : sources.isError ? (
        <div className="error-box" role="alert">
          {messageOf(sources.error)}{" "}
          <button className="link" type="button" onClick={() => sources.refetch()}>
            Try again
          </button>
        </div>
      ) : sources.data.length === 0 ? (
        <p className="sp-empty">No spiders are registered. Add one to <code>SPIDERS</code> in <code>scraper/pipeline/sources</code> and restart the API.</p>
      ) : (
        <div className="sp-table-wrap">
          <table className="sp-table">
            <thead>
              <tr>
                <th scope="col">Spider</th>
                <th scope="col">Last run</th>
                <th scope="col">Records</th>
                <th scope="col">
                  <span className="sr">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sources.data.map(({ name, latest_run: run }) => (
                <tr key={name}>
                  <td className="name">
                    <Link href={`/spiders/${name}`}>{name}</Link>
                  </td>
                  <td>
                    {run ? (
                      <>
                        <StatusPill run={run} /> <span className="muted small">{ago(run.finished_at ?? run.started_at ?? run.queued_at, now)}</span>
                      </>
                    ) : (
                      <span className="muted">Never run</span>
                    )}
                  </td>
                  <td className="muted">{run ? recordsNote(run) : "None yet"}</td>
                  <td className="act">
                    {run && isActive(run.status) ? (
                      <button className="btn btn-small" type="button" disabled={busy || !canStop(run)} onClick={() => stop.mutate(run.id)}>
                        {isStopping(run) ? "Stopping…" : "Stop"}
                      </button>
                    ) : (
                      <>
                        {run && canRetry(run) ? (
                          <button className="btn btn-small" type="button" disabled={busy} onClick={() => retry.mutate(run.id)}>
                            Retry
                          </button>
                        ) : null}
                        <button className="btn btn-small btn-primary" type="button" disabled={busy} onClick={() => start.mutate({ name, kind: "live" })}>
                          Run now
                        </button>
                        <button className="btn btn-small" type="button" disabled={busy} onClick={() => start.mutate({ name, kind: "trial" })}>
                          Trial run
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Toast message={toast} />
    </>
  );
}
```

- [ ] **Step 5: Create the run list and header.** `web/src/components/spiders/RunList.tsx`:

```tsx
"use client";

import { ago, statusLabel } from "../../lib/spiders/derive";
import type { Run } from "../../lib/spiders/types";
import { StatusPill } from "./StatusPill";

const when = (run: Run, now: number) => ago(run.finished_at ?? run.started_at ?? run.queued_at, now);

/** The runs, newest first. A list beside the run on wide screens; a select above it on narrow ones. */
export function RunList({ runs, selectedId, onSelect, now }: { runs: Run[]; selectedId: number | null; onSelect: (id: number) => void; now: number }) {
  return (
    <nav aria-label="Runs">
      <ul className="runlist">
        {runs.map((run) => (
          <li key={run.id}>
            <button type="button" aria-current={run.id === selectedId} onClick={() => onSelect(run.id)}>
              <b>#{run.id}</b>
              <StatusPill run={run} />
              <span className="when">{when(run, now)}</span>
            </button>
          </li>
        ))}
      </ul>
      <select className="runpick" aria-label="Run" value={selectedId ?? ""} onChange={(event) => onSelect(Number(event.target.value))}>
        {runs.map((run) => (
          <option key={run.id} value={run.id}>
            #{run.id} {run.kind === "trial" ? "trial " : ""}
            {statusLabel(run.status).toLowerCase()}, {when(run, now)}
          </option>
        ))}
      </select>
    </nav>
  );
}
```

`web/src/components/spiders/RunHeader.tsx`:

```tsx
"use client";

import { ago, canRetry, canStop, counters, elapsed, endNote, isStopping, statusLabel, workerNotice } from "../../lib/spiders/derive";
import type { Run } from "../../lib/spiders/types";
import { StatusPill } from "./StatusPill";

export function RunHeader({ run, now, busy, onStop, onRetry }: { run: Run; now: number; busy: boolean; onStop: () => void; onRetry: () => void }) {
  const notice = workerNotice(run, now);
  const note = endNote(run);
  const took = elapsed(run, now);
  const meta = [
    run.started_at ? `Started ${ago(run.started_at, now)}` : `Queued ${ago(run.queued_at, now)}`,
    took ? `${took} elapsed` : null,
    run.retry_of ? `Retry of #${run.retry_of}` : null,
  ].filter(Boolean);
  return (
    <section className="run-head" aria-label={`Run ${run.id}`}>
      <div className="run-title">
        <h2>Run #{run.id}</h2>
        <StatusPill run={run} />
        <div className="run-actions">
          {canStop(run) || isStopping(run) ? (
            <button className="btn btn-small" type="button" disabled={busy || !canStop(run)} onClick={onStop}>
              {isStopping(run) ? "Stopping…" : "Stop"}
            </button>
          ) : null}
          {canRetry(run) ? (
            <button className="btn btn-small" type="button" disabled={busy} onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </div>
      </div>
      <p className="muted small">{meta.join(" · ")}</p>
      <p className="sr" role="status" aria-live="polite">{`Run ${run.id} ${statusLabel(run.status).toLowerCase()}`}</p>
      {notice ? <p className="sp-notice">{notice.text}</p> : null}
      {run.error ? (
        <div className="error-box" role="alert">
          {run.error}
        </div>
      ) : null}
      {note ? <p className="muted small">{note}</p> : null}
      <dl className="totals">
        {counters(run).map((counter) => (
          <div key={counter.label}>
            <dt>{counter.label}</dt>
            <dd>{counter.value.toLocaleString("en-US")}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
```

- [ ] **Step 6: Create the ledger and the trial results.** `web/src/components/spiders/RunLedger.tsx`:

```tsx
import { hostAndPath } from "../../lib/api/format";
import { formatBytes, statusTone } from "../../lib/spiders/derive";
import type { FetchRow } from "../../lib/spiders/types";

const SHOWN = 200;

/** Every page the run has fetched, newest first, numbered in the order they were fetched. */
export function RunLedger({ rows, running }: { rows: FetchRow[] | undefined; running: boolean }) {
  const all = rows ?? [];
  const shown = all.slice(-SHOWN).reverse();
  return (
    <section aria-labelledby="ledger-title">
      <h3 id="ledger-title">Pages fetched</h3>
      {shown.length === 0 ? (
        <p className="sp-empty">{running ? "Waiting for the first page…" : "This run fetched no pages."}</p>
      ) : (
        <div className="sp-table-wrap">
          <table className="sp-ledger">
            <thead>
              <tr>
                <th className="n" scope="col">
                  <span className="sr">Order fetched</span>
                  <span aria-hidden="true">#</span>
                </th>
                <th scope="col">Status</th>
                <th scope="col">Address</th>
                <th className="num" scope="col">
                  Size
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row, index) => (
                <tr key={row.id}>
                  <td className="n">{all.length - index}</td>
                  <td>
                    <span className={`sp-code ${statusTone(row.status)}`}>{row.status}</span>
                  </td>
                  <td className="addr" title={row.url}>
                    {hostAndPath(row.url)}
                  </td>
                  <td className="num">{formatBytes(row.size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {all.length > SHOWN ? <p className="muted small">Showing the latest {SHOWN} of {all.length} pages.</p> : null}
    </section>
  );
}
```

`web/src/components/spiders/TrialResults.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { Run } from "../../lib/spiders/types";

const MAX_COLUMNS = 6;
const text = (value: unknown): string => (typeof value === "string" ? value : value === null || value === undefined ? "" : JSON.stringify(value));

/** What a trial parsed and what it rejected. A live run shows only its rejected samples. Give it `key={run.id}` so each run starts on its own first tab. */
export function TrialResults({ run }: { run: Run }) {
  const trial = run.kind === "trial";
  const items = run.stats.trial_items ?? [];
  const rejected = run.stats.rejected_samples ?? [];
  const [tab, setTab] = useState<"parsed" | "rejected">(trial ? "parsed" : "rejected");
  if (!trial && rejected.length === 0) return null;
  const columns = [...new Set(items.flatMap((item) => Object.keys(item)))].slice(0, MAX_COLUMNS);
  const byField = Object.entries(run.stats.rejected_by_field ?? {}).map(([field, count]) => `${field} ${count}`).join(", ");

  return (
    <section aria-label="What the spider parsed">
      <div className="sp-tabs" role="tablist">
        {trial ? (
          <button type="button" role="tab" id="tab-parsed" aria-selected={tab === "parsed"} aria-controls="panel-results" onClick={() => setTab("parsed")}>
            Parsed items ({items.length})
          </button>
        ) : null}
        <button type="button" role="tab" id="tab-rejected" aria-selected={tab === "rejected"} aria-controls="panel-results" onClick={() => setTab("rejected")}>
          Rejected items ({run.stats.items_rejected ?? 0})
        </button>
      </div>
      <div role="tabpanel" id="panel-results" aria-labelledby={tab === "parsed" ? "tab-parsed" : "tab-rejected"}>
        {tab === "parsed" ? (
          items.length === 0 ? (
            <p className="sp-empty">Nothing was parsed. Check the pages fetched above for a block page, then the spider's selectors.</p>
          ) : (
            <div className="sp-table-wrap">
              <table className="sp-table">
                <thead>
                  <tr>
                    {columns.map((column) => (
                      <th key={column} scope="col">
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, row) => (
                    <tr key={row}>
                      {columns.map((column) => (
                        <td key={column} title={text(item[column])}>
                          {text(item[column]).slice(0, 80)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : rejected.length === 0 ? (
          <p className="sp-empty">{(run.stats.items_rejected ?? 0) === 0 ? "No items were rejected." : "The rejected items were counted but no samples were kept."}</p>
        ) : (
          <>
            {byField ? <p className="muted small">Fields that failed: {byField}.</p> : null}
            <ul className="sp-rejects">
              {rejected.map((sample, index) => (
                <li key={index}>
                  <dl className="fields">
                    {Object.entries(sample.item).map(([key, value]) => (
                      <div key={key} style={{ display: "contents" }}>
                        <dt>{key}</dt>
                        <dd>{value}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="why">{sample.errors.join("; ")}</p>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 7: Create the spider page** `web/src/components/spiders/SpiderPage.tsx`

```tsx
"use client";

import Link from "next/link";
import { useState } from "react";
import { isActive } from "../../lib/spiders/derive";
import { Toast, useToast } from "../Toast";
import { messageOf, useFetchFeed, useRun, useRunActions, useRuns } from "./queries";
import { RunHeader } from "./RunHeader";
import { RunLedger } from "./RunLedger";
import { RunList } from "./RunList";
import { TrialResults } from "./TrialResults";
import { useNow } from "./useNow";

const CAPS = [1, 3, 5, 10, 20];

export function SpiderPage({ name }: { name: string }) {
  const runs = useRuns(name);
  const [picked, setPicked] = useState<number | null>(null); // null follows the newest run
  const [cap, setCap] = useState(5);
  const [toast, setToast] = useToast();
  const selectedId = picked ?? runs.data?.[0]?.id ?? null;
  const run = useRun(selectedId);
  const current = run.data; // a local, so TypeScript keeps the narrowing inside the handlers below
  const running = current ? isActive(current.status) : false;
  const feed = useFetchFeed(selectedId, running);
  const { start, stop, retry } = useRunActions(setToast, (created) => setPicked(created.id));
  const inFlight = runs.data?.some((item) => isActive(item.status)) ?? false;
  const now = useNow(inFlight);
  const busy = start.isPending || stop.isPending || retry.isPending;

  return (
    <>
      <div className="sp-head">
        <div>
          <Link className="sp-back small" href="/spiders">
            Spiders
          </Link>
          <h1>{name}</h1>
        </div>
        <div className="sp-controls">
          <label>
            Trial pages
            <select value={cap} onChange={(event) => setCap(Number(event.target.value))}>
              {CAPS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <button className="btn" type="button" disabled={busy || inFlight} onClick={() => start.mutate({ name, kind: "trial", maxRequests: cap })}>
            Trial run
          </button>
          <button className="btn btn-primary" type="button" disabled={busy || inFlight} onClick={() => start.mutate({ name, kind: "live" })}>
            Run now
          </button>
        </div>
      </div>
      {inFlight ? <p className="muted small">A run is already in flight, so a new one cannot start until it ends.</p> : null}

      {runs.isPending ? (
        <p role="status" className="muted">
          Loading runs…
        </p>
      ) : runs.isError ? (
        <div className="error-box" role="alert">
          {messageOf(runs.error)}{" "}
          <button className="link" type="button" onClick={() => runs.refetch()}>
            Try again
          </button>
        </div>
      ) : runs.data.length === 0 ? (
        <p className="sp-empty">No runs yet. Run a trial first to check this spider against the site.</p>
      ) : (
        <div className="sp-grid">
          <RunList runs={runs.data} selectedId={selectedId} onSelect={setPicked} now={now} />
          <div className="sp-main">
            {run.isError ? (
              <div className="error-box" role="alert">
                {messageOf(run.error)}{" "}
                <button className="link" type="button" onClick={() => run.refetch()}>
                  Try again
                </button>
              </div>
            ) : !current ? (
              <p role="status" className="muted">
                Loading run…
              </p>
            ) : (
              <>
                <RunHeader run={current} now={now} busy={busy} onStop={() => stop.mutate(current.id)} onRetry={() => retry.mutate(current.id)} />
                <RunLedger rows={feed.data} running={running} />
                <TrialResults key={current.id} run={current} />
              </>
            )}
          </div>
        </div>
      )}
      <Toast message={toast} />
    </>
  );
}
```

- [ ] **Step 8: Create the list route** `web/src/app/spiders/page.tsx`

```tsx
import type { Metadata } from "next";
import { SpiderTable } from "../../components/spiders/SpiderTable";
import { SpidersFrame } from "../../components/spiders/SpidersFrame";
import { WorkspaceProviders } from "../../components/workspace/ApiProvider";
import { getServices } from "../../lib/services";
import { requireAdmin } from "../../lib/session";

export const metadata: Metadata = { title: "Spiders" };

export default async function SpidersPage() {
  await requireAdmin();
  return (
    <WorkspaceProviders apiUrl={getServices().env.apiUrl}>
      <SpidersFrame>
        <SpiderTable />
      </SpidersFrame>
    </WorkspaceProviders>
  );
}
```

- [ ] **Step 9: Create the spider route** `web/src/app/spiders/[name]/page.tsx`

```tsx
import type { Metadata } from "next";
import { SpiderPage } from "../../../components/spiders/SpiderPage";
import { SpidersFrame } from "../../../components/spiders/SpidersFrame";
import { WorkspaceProviders } from "../../../components/workspace/ApiProvider";
import { getServices } from "../../../lib/services";
import { requireAdmin } from "../../../lib/session";

type Props = { params: Promise<{ name: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { name } = await params;
  return { title: name };
}

export default async function SpiderRoute({ params }: Props) {
  await requireAdmin();
  const { name } = await params;
  return (
    <WorkspaceProviders apiUrl={getServices().env.apiUrl}>
      <SpidersFrame>
        <SpiderPage name={name} />
      </SpidersFrame>
    </WorkspaceProviders>
  );
}
```

- [ ] **Step 10: Typecheck and run all web unit tests**

Run: `cd web && npm run typecheck && npm test`
Expected: PASS. Fix any type error in the file it names; do not loosen types with `any`.

- [ ] **Step 11: Commit**

```bash
git add web/src/components/spiders web/src/app/spiders
git commit -m "Add the spiders page: list, run history, live ledger, trial results"
```

---

### Task 9: Browser test, deployment and final checks

**Files:**
- Create: `web/e2e/specs/spiders.spec.ts`
- Modify: `web/scripts/stack.sh` (drop the `pipeline` schema with the others)
- Modify: `docs/hosted-deploy.md`, `compose.yaml`, `Containerfile`

**Interfaces:**
- Consumes: Tasks 1 to 8, the e2e helpers `createMember`, `ownerCookie`, `signInBrowser` (`web/e2e/support/journeys.ts`).
- Produces: a browser spec that runs against the real web app and API with no worker (so runs stay queued), and the deployment notes a person needs to run the worker.

- [ ] **Step 1: Make the e2e database start clean.** In `web/scripts/stack.sh`, change the `psql ... DROP SCHEMA` line so it also drops `pipeline` (the API recreates it at start-up):

```bash
  psql "$E2E_DATABASE_URL" -q -c "DROP SCHEMA IF EXISTS auth CASCADE" -c "DROP SCHEMA IF EXISTS app CASCADE" -c "DROP SCHEMA IF EXISTS pipeline CASCADE" -c "CREATE SCHEMA auth"
```

Also update the sentence in `web/scripts/e2e-ui.sh`'s header comment: "the script drops its `auth`, `app` and `pipeline` schemas first".

- [ ] **Step 2: Write the browser spec** `web/e2e/specs/spiders.spec.ts`

```ts
import { expect, test } from "@playwright/test";
import { createMember, ownerCookie, signInBrowser } from "../support/journeys";

// The stack has no worker, so a run stays queued: enough to check the page, the API and the access rules end to end.
test.describe("the spiders page", () => {
  test("is for administrators only", async ({ page, context }) => {
    const member = await createMember("spiderless");
    await signInBrowser(context, member.cookie);
    for (const path of ["/spiders", "/spiders/books_toscrape"]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/$/);
    }
    await context.clearCookies();
    await page.goto("/spiders");
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("lists the spiders, queues a trial run from the list and stops it", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders");
    await expect(page.getByRole("heading", { name: "Spiders" })).toBeVisible();
    const row = page.getByRole("row", { name: /books_toscrape/ });
    await row.getByRole("button", { name: "Trial run" }).click();
    await expect(row.getByText("Queued")).toBeVisible();
    await expect(row.getByText("Trial")).toBeVisible();
    await row.getByRole("button", { name: "Stop" }).click();
    await expect(row.getByText("Cancelled")).toBeVisible();
    await expect(row.getByRole("button", { name: "Run now" })).toBeVisible();
  });

  test("opens a spider, queues a run, stops it there and starts a trial", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders");
    await page.getByRole("link", { name: "books_toscrape" }).click();
    await expect(page).toHaveURL(/\/spiders\/books_toscrape$/);

    await page.getByRole("button", { name: "Run now" }).click();
    await expect(page.getByRole("heading", { name: /Run #\d+/ })).toBeVisible();
    await expect(page.getByText("Queued").first()).toBeVisible();
    await expect(page.getByText("A run is already in flight")).toBeVisible();
    await expect(page.getByRole("button", { name: "Run now" })).toBeDisabled();

    await page.getByRole("button", { name: "Stop" }).click();
    await expect(page.getByText("Cancelled").first()).toBeVisible();

    await page.getByRole("button", { name: "Trial run" }).click();
    await expect(page.getByText("Trial", { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "Stop" }).click(); // leave the source idle for whatever runs next
    await expect(page.getByRole("button", { name: "Run now" })).toBeEnabled();
  });
});
```

- [ ] **Step 3: Run the browser spec**

Run: `cd web && E2E_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/scraper_e2e_test npm run e2e:ui -- e2e/specs/spiders.spec.ts` (needs `make test-db` running and `psql` installed; the script says what it is missing).
Expected: 3 passed. A failure message naming `books_toscrape` missing means the API did not register the spider: look at `$LOGS/api.log` (printed on failure) for an import error from `scraper.pipeline.sources`.

- [ ] **Step 4: Update `docs/hosted-deploy.md`.**

(a) In section 1's SQL block add, after `CREATE SCHEMA auth AUTHORIZATION scraper_web;`:

```sql
CREATE SCHEMA pipeline AUTHORIZATION scraper_api;
```

(b) Replace the sentence that begins "Check the separation at any time" with this paragraph (keep the sentence before it):

```markdown
The API creates its own tables on start-up (the LangGraph checkpoint tables included) and brings the `pipeline` schema up to date. The web app creates the `auth` tables with Better Auth's migration. The spiders page needs the API to know whether someone is an administrator, so once the web app's first migration has created `auth."user"`, give the API role exactly three columns of it and nothing else:

```sql
GRANT USAGE ON SCHEMA auth TO scraper_api;
GRANT SELECT (id, role, banned) ON auth."user" TO scraper_api;
```

Check the separation at any time: connected as `scraper_api`, `SELECT email FROM auth."user"` must fail with `permission denied for table user`, and so must any read of `auth.account` (where password hashes live).
```

(c) Append a new section at the end of the file:

```markdown
## The spiders worker

The spiders page (`/spiders`, administrators only) queues runs; a separate worker process runs them. Without a worker a run stays queued, and the page says how to start one after 30 seconds.

- Start it with the API's environment: `python -m scraper.pipeline worker` (`make pipeline-worker` locally). `--concurrency N` runs up to N sources at once; a source never has two runs at the same time.
- It needs `SCRAPER_DATABASE_URL` and `SCRAPER_PIPELINE_BLOB_DIR`, a directory it can write (every fetched page is archived there, compressed). The API only reads the database, so it needs no blob directory. Run one worker per blob directory; the compose file mounts a volume at `/data/blobs`.
- Each run is a child process of the worker. Stopping the worker (SIGTERM or Ctrl+C) asks every run to stop, which ends them as stopped with what they saved. A worker or child that dies without finishing leaves a run that the next worker pass fails as `worker lost` after 60 seconds without a heartbeat.
- A browser-backed spider (`AsyncStealthySession`) needs the browsers installed on the worker host (`scrapling install`). The container image does not include them.
```

- [ ] **Step 5: Add the worker to the local stack.** In `Containerfile`, change the `useradd` line to also create the blob directory:

```dockerfile
RUN useradd --system --create-home --uid 10001 scraper && mkdir -p /data/blobs && chown scraper /data/blobs
```

In `compose.yaml` add this service after `api:` and a `blobs:` volume next to `dbdata:`:

```yaml
  worker:
    build:
      context: .
      dockerfile: Containerfile
    command: ["/app/.venv/bin/python", "-m", "scraper.pipeline", "worker"]
    env_file: .env
    environment:
      SCRAPER_DATABASE_URL: postgresql://scraper:${POSTGRES_PASSWORD}@db:5432/scraper
      SCRAPER_PIPELINE_BLOB_DIR: /data/blobs
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - blobs:/data/blobs
```

```yaml
volumes:
  dbdata:
  blobs:
```

- [ ] **Step 6: Run everything**

Run: `uv run pytest -q` then `cd web && npm run typecheck && npm test`
Expected: all PASS, no skips beyond the ones the suite already has.

- [ ] **Step 7: Look at it.** With the real stack up (`make api`, `make web`, and `make pipeline-worker` in three terminals, against your dev database; sign in as an administrator) check, and say plainly in your report which of these you could not do:
  - `/spiders`: Trial run on `books_toscrape` queues, runs and ends "Completed", "Stopped at the page cap (5 requests)", with 5 numbered ledger rows and parsed items in the Parsed tab.
  - Run now, then Stop: the run ends "Stopped" with what it had saved; Retry starts a new run that says "Retry of #N".
  - Stop the worker while a run is going (Ctrl+C): the run ends "Stopped". Kill -9 the worker: after about a minute the page shows "Failed" with "worker lost".
  - Widths 1280 px and 400 px, light and dark: no horizontal page scroll, the run select replaces the list under 56rem, focus rings visible, the pulsing dot stops with reduced motion on.

- [ ] **Step 8: Commit**

```bash
git add web/e2e/specs/spiders.spec.ts web/scripts/stack.sh web/scripts/e2e-ui.sh docs/hosted-deploy.md compose.yaml Containerfile
git commit -m "Test the spiders page in the browser; document and compose the worker"
```

---

## Spec coverage

| Spec section | Where |
|---|---|
| 4 Data model (columns, statuses, stats, trial items) | Tasks 1, 3 |
| 5 Repositories (enqueue/SourceBusy, claim, heartbeat, cancel, reap, history, fetches) | Task 2 |
| 6 Worker and execution (loop, child, heartbeat, stop once, CLI keeps working and obeys the one-run rule) | Tasks 4, 5 |
| 7 Trial runs (cap, archive, 25 items, no records, completed at cap, retry as trial) | Tasks 3, 6 |
| 8 API (admin check, routes, errors, spider registry filter, start-up migration) | Task 6 |
| 8 column grant and `pipeline` schema for the split roles | Task 9 (docs) |
| 9 Page (routes, list, master-detail, problem states, polling, a11y, files) | Tasks 7, 8 |
| 10 Testing (queue, heartbeat, trial, execution, API, migration; Vitest; Playwright) | Tasks 1 to 9 |
| 11 Open items: 1 fetch duration (resolved: omitted), 2 realtor spider (filtered, untouched), 3 deployment, 4 thresholds (constants in `derive.ts` and `runner.py`/`worker.py`) | Tasks 6, 7, 9 |

Deviations from the spec, deliberate: the run history and list poll every 6 s (the spec's own amendment); the stale-run reaper and one-run-per-source rule also protect CLI runs (the spec says so in section 6).
