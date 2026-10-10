import threading
from datetime import date

import pytest

from fakes import make_page
from scraper.config import Config
from scraper.models import Message, TraceEntry
from scraper.store import RunRecord
from scraper.usage import UsageLimitReached, UsageMeter
from scraper_api.pgstore import PgSessionStore, PgUsageStore

CFG = Config(max_stored_pages=3, max_page_chars=50, max_result_rows=4)


@pytest.fixture
def store(pg):
    return PgSessionStore(pg, "alice", CFG)


def test_a_user_id_is_required(pg):
    with pytest.raises(ValueError):
        PgSessionStore(pg, "", CFG)


def test_pages_keep_their_order_and_upsert_by_normalized_url(store):
    store.save_page(make_page("https://a.test/x", title="A", markdown="# A", links=["https://a.test/y"], html="<h1>A</h1>"))
    store.save_page(make_page("https://a.test/z", title="Z"))
    store.save_page(make_page("https://a.test/x/", title="A again", markdown="# A2"))
    assert [p.title for p in store.load().pages] == ["A again", "Z"]
    store.save_page(make_page("https://a.test/x", title="A", markdown="# A", links=["https://a.test/y"], html="<h1>A</h1>"))
    first = store.load().pages[0]
    assert first.links == ("https://a.test/y",) and first.html == "<h1>A</h1>" and first.looks_empty is False


def test_rows_and_history_round_trip(store):
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


def test_run_record_round_trip(store):
    assert store.load_run() is None
    record = RunRecord(number=1, instruction="do it", model="m", system_prompt="sys", seed_url="a.test", status="paused",
                       pending={"kind": "ask", "question": "Which?"}, allowed_hosts=["a.test"], fetched=3,
                       fetched_urls=["https://a.test/"], context_sources=["https://a.test/"], rows_added=2,
                       trace=[{"name": "fetch_page", "args": {"url": "u"}, "text": "t", "ok": True}],
                       followups=[{"id": "f1", "text": "also the SKU", "delivered": False}])
    store.save_run(record)
    assert store.load_run() == record
    store.save_run(RunRecord(number=2, instruction="again"))
    assert store.load_run().number == 2  # saving replaces, it does not add


def test_session_id_is_stable_starts_with_the_user_and_run_numbers_count_up(store, pg):
    first = store.session_id()
    assert first == store.session_id() == PgSessionStore(pg, "alice", CFG).session_id()
    assert first.startswith("alice:")
    assert (store.run_counter(), store.next_run_number(), store.next_run_number(), store.run_counter()) == (0, 1, 2, 2)


def test_wipe_forgets_the_session_but_keeps_preferences(store):
    old = store.session_id()
    store.set_pref("provider", "mistral")
    store.save_page(make_page("https://a.test/"))
    store.append_rows([{"a": 1}])
    store.append_history([Message("user", "hi"), Message("assistant", "yo")])
    store.next_run_number()
    store.save_run(RunRecord(number=1, instruction="x"))
    store.wipe()
    loaded = store.load()
    assert (loaded.pages, loaded.rows, loaded.history, loaded.run) == ([], [], [], None)
    assert store.run_counter() == 0 and store.session_id() != old
    assert store.get_pref("provider") == "mistral"


def test_preferences_persist_and_are_replaced(store):
    assert store.get_pref("provider") is None
    store.set_pref("provider", "mistral")
    store.set_pref("provider", "other")
    assert store.get_pref("provider") == "other"


def test_writes_from_several_threads_do_not_fail(store):
    errors = []

    def work(n):
        try:
            store.append_rows([{"n": n}])
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(store.load().rows) == 4


# ---- users never see each other ---------------------------------------------------


def test_two_users_have_completely_separate_data(pg):
    alice, bob = PgSessionStore(pg, "alice", CFG), PgSessionStore(pg, "bob", CFG)
    alice.save_page(make_page("https://a.test/", title="Alice page"))
    alice.append_rows([{"owner": "alice"}])
    alice.append_history([Message("user", "alice question")])
    alice.save_run(RunRecord(number=1, instruction="alice task"))
    alice.set_pref("provider", "mistral")
    assert alice.session_id() != bob.session_id()
    loaded = bob.load()
    assert (loaded.pages, loaded.rows, loaded.history, loaded.run) == ([], [], [], None)
    assert bob.get_pref("provider") is None and bob.run_counter() == 0


def test_wiping_one_user_leaves_the_other_alone(pg):
    alice, bob = PgSessionStore(pg, "alice", CFG), PgSessionStore(pg, "bob", CFG)
    for who in (alice, bob):
        who.save_page(make_page("https://a.test/"))
        who.append_rows([{"n": 1}])
        who.save_run(RunRecord(number=1, instruction="x"))
    alice.wipe()
    assert alice.load().pages == [] and len(bob.load().pages) == 1
    assert bob.load_run() is not None and len(bob.load().rows) == 1


def test_the_same_page_url_can_be_stored_by_two_users(pg):
    alice, bob = PgSessionStore(pg, "alice", CFG), PgSessionStore(pg, "bob", CFG)
    alice.save_page(make_page("https://a.test/", title="Alice's copy"))
    bob.save_page(make_page("https://a.test/", title="Bob's copy"))
    assert [p.title for p in alice.load().pages] == ["Alice's copy"]
    assert [p.title for p in bob.load().pages] == ["Bob's copy"]


# ---- caps ---------------------------------------------------------------------------


def test_stored_page_text_is_cut_to_the_cap(store):
    store.save_page(make_page("https://a.test/", markdown="m" * 500, html="h" * 500))
    page = store.load().pages[0]
    assert len(page.markdown) == 50 and len(page.html) == 50


def test_a_page_beyond_the_stored_page_cap_is_refused_but_an_existing_one_can_be_updated(store):
    for n in range(3):
        store.save_page(make_page(f"https://a.test/{n}"))
    with pytest.raises(UsageLimitReached, match="3 pages stored"):
        store.save_page(make_page("https://a.test/3"))
    store.save_page(make_page("https://a.test/1", title="updated"))  # not a new page
    assert len(store.load().pages) == 3


def test_rows_beyond_the_cap_are_refused_without_storing_any_of_the_batch(store):
    store.append_rows([{"n": 1}, {"n": 2}, {"n": 3}])
    with pytest.raises(UsageLimitReached, match="most allowed"):
        store.append_rows([{"n": 4}, {"n": 5}])
    assert len(store.load().rows) == 3
    store.append_rows([{"n": 4}])
    assert len(store.load().rows) == 4


def test_the_caps_are_per_user(pg):
    alice, bob = PgSessionStore(pg, "alice", CFG), PgSessionStore(pg, "bob", CFG)
    alice.append_rows([{"n": n} for n in range(4)])
    bob.append_rows([{"n": 1}])  # bob is nowhere near his own cap
    with pytest.raises(UsageLimitReached):
        alice.append_rows([{"n": 9}])


# ---- usage counters -----------------------------------------------------------------


def test_usage_counts_up_to_the_limit_and_stops(pg):
    usage = PgUsageStore(pg)
    day = date(2026, 10, 9)
    assert [usage.try_add("alice", day, "pages", 2) for _ in range(3)] == [True, True, False]
    assert usage.get("alice", day) == {"model_calls": 0, "pages": 2}
    assert usage.get("alice", date(2026, 10, 10)) == {"model_calls": 0, "pages": 0}
    assert usage.get("bob", day) == {"model_calls": 0, "pages": 0}


def test_usage_is_never_over_the_limit_when_threads_race(pg):
    usage = PgUsageStore(pg)
    day = date(2026, 10, 9)
    results = []

    def spend():
        results.append(usage.try_add("alice", day, "model_calls", 5))

    threads = [threading.Thread(target=spend) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 5 and usage.get("alice", day)["model_calls"] == 5


def test_usage_rejects_unknown_fields_and_non_positive_limits(pg):
    usage = PgUsageStore(pg)
    with pytest.raises(ValueError):
        usage.try_add("alice", date(2026, 10, 9), "pages; DROP TABLE app.usage", 5)
    assert usage.try_add("alice", date(2026, 10, 9), "pages", 0) is False


def test_the_meter_works_on_top_of_the_postgres_counters(pg):
    meter = UsageMeter("alice", PgUsageStore(pg), Config(daily_pages=1), today=lambda: date(2026, 10, 9))
    meter.charge_page()
    with pytest.raises(UsageLimitReached):
        meter.charge_page()
    assert meter.snapshot()["pages"] == 1


# ---- a closed store (the user is being deleted) ignores late writes ------------------------------------------------------------


def test_a_closed_store_ignores_every_write_but_still_reads(pg):
    store = PgSessionStore(pg, "alice", CFG)
    store.save_page(make_page("https://a.test/", title="before"))
    store.set_pref("provider", "mistral")
    store.close()
    store.save_page(make_page("https://a.test/late"))
    store.append_rows([{"late": 1}])
    store.append_history([Message("user", "late")])
    store.save_run(RunRecord(number=1, instruction="late"))
    store.set_pref("late", "1")
    assert store.next_run_number() == 0
    assert [p.title for p in store.load().pages] == ["before"]  # reads still work
    assert (store.load().rows, store.load().history, store.load().run) == ([], [], None)
    assert store.get_pref("late") is None and store.get_pref("provider") == "mistral"


def test_a_closed_store_does_not_create_a_session_row(pg):
    store = PgSessionStore(pg, "alice", CFG)
    store.close()
    store.session_id()
    store.wipe()
    with pg.connection() as conn:
        assert conn.execute("SELECT count(*) FROM app.sessions WHERE user_id = 'alice'").fetchone()[0] == 0


def test_rows_keep_the_order_of_their_columns_after_a_reload(pg):
    # Postgres jsonb sorts keys (shortest first), which would scramble the table's columns once a session is loaded again.
    PgSessionStore(pg, "alice", CFG).append_rows([{"price": "$9", "plan": "Solo", "page": "shop.example/plans"}])
    reloaded = PgSessionStore(pg, "alice", CFG).load()
    assert list(reloaded.rows[0]) == ["price", "plan", "page"]
