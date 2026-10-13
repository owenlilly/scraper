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
    launched = []

    def launch(run_id):
        launched.append(run_id)

        def finish():
            with uow() as work:
                work.runs.finish(run_id, "completed", {})
        return FakeChild(code=0, on_start=finish)

    worker = Worker(pipeline, blobs, launch=launch)
    worker.tick()
    assert launched == [first.id]  # concurrency 1: the second run waits for the first process to go
    assert status_of(uow, first.id).status == "completed"  # the process finished its run before exiting: nothing to correct
    assert status_of(uow, second.id).status == "queued"
    worker.tick()
    assert launched == [first.id, second.id] and status_of(uow, second.id).status == "completed"


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
