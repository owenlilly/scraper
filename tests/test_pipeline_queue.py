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


def test_a_run_the_reaper_failed_stays_failed_when_its_slow_process_finally_finishes(uow):
    shop = source(uow)
    with uow() as work:
        run = work.runs.start(work.runs.enqueue(shop.id).id, "w")
        work.session.execute(update(RunRow).where(RunRow.id == run.id).values(heartbeat_at=RunRow.heartbeat_at - timedelta(minutes=5)))
    with uow() as work:
        work.runs.reap_stale(timedelta(seconds=60))
    with uow() as work:
        late = work.runs.finish(run.id, "completed", {"requests_count": 99})
    assert (late.status, late.error, late.stats) == ("failed", "worker lost", {})
    with uow() as work:
        assert work.runs.get(run.id).status == "failed"
