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
