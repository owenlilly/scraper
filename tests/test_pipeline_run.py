"""A whole run: a local site, a spider, the archive, the records."""

import pytest
from sqlalchemy import func, select

from pipeline_support import book, local_books, serve, write_site
from scraper.pipeline.blobs import FilesystemBlobStore
from scraper.pipeline.models import FetchRow, RecordVersionRow
from scraper.pipeline.runner import run_source
from scraper.pipeline.uow import UnitOfWork

PAGES = [[book(1), book(2), book(3)], [book(4, price="£20.00"), book(5, in_stock=False)]]


@pytest.fixture
def site(tmp_path):
    write_site(tmp_path / "site", PAGES)
    with serve(tmp_path / "site") as base:
        yield base, tmp_path / "site"


def crawl(pipeline, blobs, base, **kwargs):
    return run_source(local_books(base, **kwargs), pipeline, blobs)


def source_id(pipeline, blobs):
    with UnitOfWork(pipeline, blobs) as uow:
        return uow.sources.get("books_toscrape").id


def test_a_run_stores_every_record_and_archives_every_page(pipeline, blobs, site):
    base, _ = site
    run = crawl(pipeline, blobs, base)
    assert run.status == "completed" and run.error is None
    assert run.stats["records_created"] == 5 and run.stats["items_rejected"] == 0
    with UnitOfWork(pipeline, blobs) as uow:
        sid = source_id(pipeline, blobs)
        assert uow.records.count(sid) == 5
        last = uow.records.current(sid, f"{base}/catalogue/book-5_5/index.html")
        assert last.data["price"] == "10.00" and last.data["in_stock"] is False and last.data["rating"] == 3
        assert uow.session.scalar(select(func.count()).select_from(FetchRow).where(FetchRow.run_id == run.id, FetchRow.status == 200)) == 2


def test_every_record_points_at_the_archived_page_it_was_parsed_from(pipeline, blobs, site):
    base, _ = site
    crawl(pipeline, blobs, base)
    with UnitOfWork(pipeline, blobs) as uow:
        rows = uow.session.execute(select(RecordVersionRow.data["title"].astext, RecordVersionRow.fetch_id)).all()
        assert len(rows) == 5
        for title, fetch_id in rows:
            assert f'title="{title}"'.encode() in uow.fetches.body(fetch_id)


def test_the_item_to_page_link_holds_when_many_pages_are_fetched_at_once(pipeline, blobs, tmp_path):
    pages = [[book(n * 10 + i) for i in range(1, 4)] for n in range(1, 13)]
    write_site(tmp_path / "wide", pages, chained=False)
    with serve(tmp_path / "wide", delay=0.05) as base:
        run = crawl(pipeline, blobs, base, pages=12, concurrent_requests=6)
    assert run.status == "completed" and run.stats["records_created"] == 36
    with UnitOfWork(pipeline, blobs) as uow:
        rows = uow.session.execute(select(RecordVersionRow.data["title"].astext, RecordVersionRow.fetch_id)).all()
        assert len(rows) == 36
        for title, fetch_id in rows:
            assert f'title="{title}"'.encode() in uow.fetches.body(fetch_id)


def test_running_again_on_an_unchanged_site_adds_no_versions_and_no_blobs(pipeline, blobs, site):
    base, _ = site
    crawl(pipeline, blobs, base)
    files_before = sorted(p.name for p in blobs.root.rglob("*.zst"))
    again = crawl(pipeline, blobs, base)
    assert again.stats["records_created"] == 0 and again.stats["records_changed"] == 0 and again.stats["records_unchanged"] == 5
    with UnitOfWork(pipeline, blobs) as uow:
        assert uow.session.scalar(select(func.count()).select_from(RecordVersionRow)) == 5
        assert uow.session.scalar(select(func.count()).select_from(FetchRow)) >= 4  # both runs' fetches are kept
    assert sorted(p.name for p in blobs.root.rglob("*.zst")) == files_before  # identical bodies are stored once


def test_a_changed_price_becomes_a_second_version_with_its_own_archived_page(pipeline, blobs, site):
    base, root = site
    crawl(pipeline, blobs, base)
    changed = [list(PAGES[0]), list(PAGES[1])]
    changed[0][0] = book(1, price="£12.50")
    write_site(root, changed)
    run = crawl(pipeline, blobs, base)
    assert run.stats["records_changed"] == 1 and run.stats["records_unchanged"] == 4
    with UnitOfWork(pipeline, blobs) as uow:
        history = uow.records.history(source_id(pipeline, blobs), f"{base}/catalogue/book-1_1/index.html")
        assert [v.data["price"] for v in history] == ["10.00", "12.50"]
        assert history[0].fetch_id != history[1].fetch_id
        assert b"12.50" in uow.fetches.body(history[1].fetch_id) and b"12.50" not in uow.fetches.body(history[0].fetch_id)


def test_an_item_that_fails_validation_is_counted_and_not_stored(pipeline, blobs, tmp_path):
    write_site(tmp_path / "bad", [[book(1), book(2, price="call us"), book(3)]])
    with serve(tmp_path / "bad") as base:
        run = crawl(pipeline, blobs, base)
    assert run.status == "completed"
    assert run.stats["records_created"] == 2 and run.stats["items_rejected"] == 1
    assert run.stats["rejected_by_field"] == {"price": 1}
    assert run.stats["rejected_samples"][0]["item"]["price"] == "call us"


def test_broken_storage_fails_the_run_instead_of_pretending(pipeline, tmp_path, site):
    base, _ = site
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("in the way")
    run = run_source(local_books(base), pipeline, FilesystemBlobStore(not_a_directory))
    assert run.status == "failed" and "archiving" in run.error
    with UnitOfWork(pipeline, FilesystemBlobStore(tmp_path / "ok")) as uow:
        assert uow.records.count(source_id(pipeline, FilesystemBlobStore(tmp_path / "ok"))) == 0
