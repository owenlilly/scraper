"""A trial run: the real spider, a few pages, and nothing stored but the evidence."""

import signal
from types import SimpleNamespace

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


def test_stop_once_does_not_pass_on_a_pause_when_the_engine_is_already_pausing(pipeline, blobs):
    """A Ctrl+C or a worker shutdown pauses the engine behind our back; a Stop from the page after it would be a force stop."""
    spider = local_books("http://x")(ctx=RunContext(pipeline, blobs, 1, 1))
    spider._engine = SimpleNamespace(_pause_requested=True)
    calls = []
    spider.pause = lambda: calls.append(1)
    assert spider.stop_once() is True and calls == []


def test_a_sigint_while_a_stop_is_already_draining_is_not_a_force_stop(pipeline, blobs):
    """Stop from the page, then the worker is restarted and signals the child: the second request must not cancel in-flight work."""
    spider = local_books("http://x")(ctx=RunContext(pipeline, blobs, 1, 1))
    calls = []
    spider._engine = SimpleNamespace(request_pause=lambda: calls.append("engine"), _pause_requested=False)
    spider._stop_asked = True
    spider._setup_signal_handler()
    try:
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
    finally:
        spider._restore_signal_handler()
    assert calls == []


def test_a_sigint_with_no_stop_underway_still_pauses_the_crawl_once(pipeline, blobs):
    spider = local_books("http://x")(ctx=RunContext(pipeline, blobs, 1, 1))
    calls = []
    spider._engine = SimpleNamespace(request_pause=lambda: calls.append("engine"), _pause_requested=False)
    spider._setup_signal_handler()
    try:
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
    finally:
        spider._restore_signal_handler()
    assert calls == ["engine"]


def test_a_trial_holds_to_its_cap_even_when_many_pages_could_be_fetched_at_once(pipeline, blobs, tmp_path):
    write_site(tmp_path / "wide", [[book(n)] for n in range(1, 13)], chained=False)
    with serve(tmp_path / "wide", delay=0.05) as base:
        run = run_source(local_books(base, pages=12, concurrent_requests=6), pipeline, blobs, kind="trial", options={"max_requests": 2})
    assert run.stats["requests_count"] == 2 and counts(pipeline, blobs)["fetches"] == 2
