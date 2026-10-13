import pytest

from scraper.pipeline.domain import RecordItem
from scraper.pipeline.uow import UnitOfWork


def item(key: str, value: str, fetch_id: int | None = None) -> RecordItem:
    return RecordItem(key, {"value": value}, f"hash-{value}", fetch_id)


@pytest.fixture
def uow(pipeline, blobs):
    return lambda: UnitOfWork(pipeline, blobs)


def test_ensure_creates_a_source_once_and_keeps_operator_settings(uow):
    with uow() as work:
        first = work.sources.ensure("shop", {"delay": 1})
    with uow() as work:
        again = work.sources.ensure("shop", {"delay": 99})
        assert again.id == first.id
        assert again.settings == {"delay": 1}  # a code default must not overwrite what an operator set
        assert work.sources.get("missing") is None


def test_a_new_record_changed_record_and_repeat_sighting(uow):
    with uow() as work:
        source = work.sources.ensure("shop")
        run = work.runs.enqueue(source.id)
        created = work.records.upsert_batch(source.id, run.id, "1", [item("a", "x"), item("b", "x")])
        unchanged = work.records.upsert_batch(source.id, run.id, "1", [item("a", "x")])
        changed = work.records.upsert_batch(source.id, run.id, "1", [item("a", "y")])
    assert (created.created, created.changed, created.unchanged) == (2, 0, 0)
    assert (unchanged.created, unchanged.changed, unchanged.unchanged) == (0, 0, 1)
    assert (changed.created, changed.changed, changed.unchanged) == (0, 1, 0)
    with uow() as work:
        assert work.records.count(source.id) == 2
        assert [v.data["value"] for v in work.records.history(source.id, "a")] == ["x", "y"]
        assert work.records.current(source.id, "a").data == {"value": "y"}
        assert len(work.records.history(source.id, "b")) == 1


def test_a_key_repeated_within_a_batch_collapses_and_the_last_one_wins(uow):
    with uow() as work:
        source = work.sources.ensure("shop")
        run = work.runs.enqueue(source.id)
        result = work.records.upsert_batch(source.id, run.id, "1", [item("a", "first"), item("a", "second")])
        assert (result.created, result.changed, result.unchanged) == (1, 0, 0)  # one statement cannot touch a row twice
        assert work.records.count(source.id) == 1
        assert work.records.current(source.id, "a").data == {"value": "second"}


def test_hostile_text_in_scraped_values_is_stored_as_data_and_never_run_as_sql(uow):
    hostile = "x'); DROP TABLE pipeline.record; -- \\ %s %(x)s {0} :name"
    with uow() as work:
        source = work.sources.ensure(hostile)  # a source name is also text that came from outside
        run = work.runs.enqueue(source.id)
        work.records.upsert_batch(source.id, run.id, hostile, [RecordItem(hostile, {"title": hostile}, "hash-h")])
    with uow() as work:
        assert work.sources.get(hostile).id == source.id
        stored = work.records.current(source.id, hostile)
        assert stored.data == {"title": hostile} and stored.parser_version == hostile
        assert work.records.count(source.id) == 1  # and the table is still there


def test_a_big_batch_is_split_into_chunks_and_still_counted_once(uow):
    from scraper.pipeline.repositories.records import CHUNK

    many = [item(f"k{n}", "x") for n in range(CHUNK * 2 + 7)]
    with uow() as work:
        source = work.sources.ensure("shop")
        run = work.runs.enqueue(source.id)
        assert work.records.upsert_batch(source.id, run.id, "1", many).created == len(many)
        assert work.records.upsert_batch(source.id, run.id, "1", many).unchanged == len(many)
        assert work.records.count(source.id) == len(many)


def test_records_with_the_same_key_in_different_sources_are_separate(uow):
    with uow() as work:
        one, two = work.sources.ensure("one"), work.sources.ensure("two")
        run = work.runs.enqueue(one.id)
        work.records.upsert_batch(one.id, run.id, "1", [item("a", "x")])
        work.records.upsert_batch(two.id, run.id, "1", [item("a", "z")])
        assert work.records.current(one.id, "a").data == {"value": "x"}
        assert work.records.current(two.id, "a").data == {"value": "z"}


def test_a_failed_unit_of_work_rolls_everything_back(uow):
    with uow() as work:
        source = work.sources.ensure("shop")
        run = work.runs.enqueue(source.id)
    with pytest.raises(RuntimeError):
        with uow() as work:
            work.records.upsert_batch(source.id, run.id, "1", [item("a", "x")])
            raise RuntimeError("boom")
    with uow() as work:
        assert work.records.count(source.id) == 0


def test_a_run_goes_from_queued_to_running_to_finished(uow):
    with uow() as work:
        source = work.sources.ensure("shop")
        run = work.runs.enqueue(source.id)
        assert run.status == "queued" and run.started_at is None
        running = work.runs.start(run.id, "worker-1")
        assert running.status == "running" and running.started_at is not None
        done = work.runs.finish(run.id, "completed", {"requests_count": 3})
        assert done.status == "completed" and done.finished_at is not None and done.stats == {"requests_count": 3}
        assert work.runs.get(run.id) == done
        assert work.runs.get(run.id + 100) is None


def test_a_run_cannot_finish_as_something_else(uow):
    with uow() as work:
        run = work.runs.enqueue(work.sources.ensure("shop").id)
        with pytest.raises(ValueError):
            work.runs.finish(run.id, "queued", {})


def test_a_fetch_keeps_its_metadata_and_a_retrievable_body(uow):
    with uow() as work:
        run = work.runs.enqueue(work.sources.ensure("shop").id)
        ref = work.fetches.save(
            run.id, url="http://a/x", final_url="http://a/y", status=200, headers={"content-type": "text/html"},
            body=b"<p>hi</p>", fetcher="scrapling",
        )
        assert ref.size == 9
        assert work.fetches.body(ref.id) == b"<p>hi</p>"
        with pytest.raises(KeyError):
            work.fetches.body(ref.id + 100)
