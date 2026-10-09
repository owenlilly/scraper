import stat
import threading

from fakes import make_page
from scraper.models import Message, TraceEntry
from scraper.store import RunRecord, SessionStore


def test_pages_keep_their_order_and_upsert_by_normalized_url(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    store.save_page(make_page("https://a.test/x", title="A", markdown="# A", links=["https://a.test/y"], html="<h1>A</h1>"))
    store.save_page(make_page("https://a.test/z", title="Z"))
    store.save_page(make_page("https://a.test/x/", title="A again", markdown="# A2"))
    pages = store.load().pages
    assert [p.title for p in pages] == ["A again", "Z"]
    store.save_page(make_page("https://a.test/x", title="A", markdown="# A", links=["https://a.test/y"], html="<h1>A</h1>"))
    first = store.load().pages[0]
    assert first.links == ("https://a.test/y",) and first.html == "<h1>A</h1>" and first.looks_empty is False


def test_rows_and_history_round_trip(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    store.append_rows([{"name": "Starter", "price": "$10"}])
    store.append_rows([{"name": "Team", "tags": ["a", "b"]}])
    trace = (TraceEntry("fetch_page", {"url": "u"}, "page text", True),)
    store.append_history(
        [
            Message("user", "scrape it"),
            Message("assistant", "done", "thought", ("https://a.test/",), interrupted=False, note="2 rows added to the results table", trace=trace),
        ]
    )
    loaded = store.load()
    assert loaded.rows == [{"name": "Starter", "price": "$10"}, {"name": "Team", "tags": ["a", "b"]}]
    user, assistant = loaded.history
    assert (user.role, user.content) == ("user", "scrape it")
    assert assistant.reasoning == "thought" and assistant.sources == ("https://a.test/",)
    assert assistant.note == "2 rows added to the results table" and assistant.trace == trace


def test_run_record_round_trip_and_resumable_statuses(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    assert store.load_run() is None
    record = RunRecord(number=1, instruction="do it", model="m", system_prompt="sys", seed_url="a.test", status="paused",
                       pending={"kind": "ask", "question": "Which?"}, allowed_hosts=["a.test"], fetched=3,
                       fetched_urls=["https://a.test/"], context_sources=["https://a.test/"], rows_added=2,
                       trace=[{"name": "fetch_page", "args": {"url": "u"}, "text": "t", "ok": True}])
    store.save_run(record)
    assert store.load_run() == record and record.resumable
    for status, resumable in [("running", True), ("paused", True), ("stopped", True), ("error", True), ("done", False), ("ended", False)]:
        assert RunRecord(number=1, instruction="x", status=status).resumable is resumable


def test_session_id_is_stable_and_run_numbers_count_up(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    first = store.session_id()
    assert first == store.session_id() == SessionStore(tmp_path / "s.db").session_id()
    assert (store.run_counter(), store.next_run_number(), store.next_run_number(), store.run_counter()) == (0, 1, 2, 2)


def test_wipe_forgets_everything_including_the_session_id(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    old = store.session_id()
    store.save_page(make_page("https://a.test/"))
    store.append_rows([{"a": 1}])
    store.append_history([Message("user", "hi"), Message("assistant", "yo")])
    store.next_run_number()
    store.save_run(RunRecord(number=1, instruction="x"))
    store.wipe()
    loaded = store.load()
    assert (loaded.pages, loaded.rows, loaded.history, loaded.run) == ([], [], [], None)
    assert store.run_counter() == 0 and store.session_id() != old


def test_a_corrupt_database_is_set_aside_and_a_fresh_one_started(tmp_path):
    # Review Focus 8
    path = tmp_path / "s.db"
    path.write_bytes(b"this is definitely not an sqlite database" * 50)
    store = SessionStore(path)
    assert store.recovered is True
    assert store.load().pages == []
    assert list(tmp_path.glob("s.db.corrupt-*"))
    store.save_page(make_page("https://a.test/"))
    assert len(store.load().pages) == 1


def test_a_missing_data_directory_is_created_private(tmp_path):
    target = tmp_path / "nested" / "data"
    store = SessionStore(target / "s.db")
    assert store.recovered is False
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_writes_from_several_threads_do_not_fail(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    errors = []

    def work(n):
        try:
            store.save_page(make_page(f"https://a.test/{n}"))
            store.append_rows([{"n": n}])
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    loaded = store.load()
    assert errors == [] and len(loaded.pages) == 8 and len(loaded.rows) == 8
