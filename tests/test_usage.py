from datetime import date

import pytest

from scraper.config import Config
from scraper.usage import MemoryUsageStore, UsageLimitReached, UsageMeter

CFG = Config(daily_model_calls=3, daily_pages=2)


def meter(user="u1", store=None, day=date(2026, 10, 9)):
    return UsageMeter(user, store or MemoryUsageStore(), CFG, today=lambda: day)


def test_model_calls_are_counted_up_to_the_daily_limit_then_refused():
    m = meter()
    for _ in range(3):
        m.charge_model_call()
    with pytest.raises(UsageLimitReached, match=r"model-call limit .*3"):
        m.charge_model_call()
    assert m.snapshot()["model_calls"] == 3  # the refused call is not counted


def test_pages_are_counted_separately_from_model_calls():
    m = meter()
    m.charge_page()
    m.charge_page()
    with pytest.raises(UsageLimitReached, match="page limit"):
        m.charge_page()
    m.charge_model_call()  # unaffected


def test_the_message_says_when_it_resets_and_that_the_run_is_kept():
    m = meter()
    for _ in range(2):
        m.charge_page()
    with pytest.raises(UsageLimitReached) as caught:
        m.charge_page()
    assert "00:00 UTC" in str(caught.value) and "saved" in str(caught.value)


def test_counts_start_again_the_next_utc_day():
    store = MemoryUsageStore()
    for _ in range(2):
        meter(store=store).charge_page()
    with pytest.raises(UsageLimitReached):
        meter(store=store).charge_page()
    meter(store=store, day=date(2026, 10, 10)).charge_page()


def test_users_are_counted_separately():
    store = MemoryUsageStore()
    for _ in range(2):
        meter("alice", store).charge_page()
    meter("bob", store).charge_page()
    with pytest.raises(UsageLimitReached):
        meter("alice", store).charge_page()


def test_snapshot_reports_used_limits_and_the_reset_time():
    m = meter()
    m.charge_model_call()
    m.charge_page()
    assert m.snapshot() == {
        "day": "2026-10-09",
        "model_calls": 1, "model_calls_limit": 3,
        "pages": 1, "pages_limit": 2,
        "resets_at": "2026-10-10T00:00:00+00:00",
    }


def test_exhausted_names_the_first_spent_allowance_or_none():
    m = meter()
    assert m.exhausted() is None
    for _ in range(2):
        m.charge_page()
    assert "page limit" in m.exhausted()
    for _ in range(3):
        m.charge_model_call()
    assert "model-call limit" in m.exhausted()
