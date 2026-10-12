import asyncio

import pytest

from fakes import FakeLLM, make_fetch, make_page
from scraper.config import Config
from scraper.usage import MemoryUsageStore
from scraper.api.registry import RateLimited, RateLimiter, RunSlots, SessionRegistry, TooBusy

A = "https://site.test/a"
CFG = Config(hosted=True, request_delay=0.0, max_stored_pages=10)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def make_registry(pg):
    def build(clock=None, **kw):
        web = {A: make_page(A, markdown="# A\n\nText of A.")}
        return SessionRegistry(
            CFG,
            pool=pg,
            checkpointer=None,
            llm=FakeLLM(),
            usage_store=MemoryUsageStore(),
            clock=clock or Clock(),
            fetch_base=make_fetch(web),
            url_check=lambda url: None,
            **kw,
        )

    return build


async def test_a_user_gets_the_same_session_every_time(make_registry):
    registry = make_registry()
    first = await registry.get("alice")
    assert await registry.get("alice") is first
    assert await registry.get("bob") is not first


async def test_simultaneous_first_requests_create_one_session(make_registry):
    registry = make_registry()
    entries = await asyncio.gather(*(registry.get("alice") for _ in range(8)))
    assert len({id(e) for e in entries}) == 1 and len(registry) == 1


async def test_users_sessions_do_not_share_pages(make_registry):
    registry = make_registry()
    alice, bob = await registry.get("alice"), await registry.get("bob")
    await alice.session.scrape(A)
    assert len(alice.session.pages) == 1 and bob.session.pages == []


async def test_an_idle_session_is_evicted_and_comes_back_from_postgres(make_registry):
    clock = Clock()
    registry = make_registry(clock, idle_seconds=60)
    entry = await registry.get("alice")
    await entry.session.scrape(A)
    clock.now += 61
    assert registry.evict_idle() == 1 and len(registry) == 0
    restored = await registry.get("alice")
    assert restored is not entry
    assert [p.url for p in restored.session.pages] == [A]


async def test_a_session_in_use_is_never_evicted(make_registry):
    clock = Clock()
    registry = make_registry(clock, idle_seconds=60)
    entry = await registry.get("alice")
    entry.session.busy = True
    clock.now += 600
    assert registry.evict_idle() == 0
    entry.session.busy = False
    assert registry.evict_idle() == 1


async def test_a_recently_used_session_is_kept(make_registry):
    clock = Clock()
    registry = make_registry(clock, idle_seconds=60)
    await registry.get("alice")
    clock.now += 50
    await registry.get("alice")
    clock.now += 50
    assert registry.evict_idle() == 0


async def test_the_sessions_fetch_blocks_private_addresses_and_counts_pages(pg):
    registry = SessionRegistry(
        Config(hosted=True, request_delay=0.0, daily_pages=1), pool=pg, checkpointer=None, llm=FakeLLM(),
        usage_store=MemoryUsageStore(), fetch_base=make_fetch({A: make_page(A)}),
    )  # the real address check is left in place
    entry = await registry.get("alice")
    with pytest.raises(Exception, match="not a public address|Could not find"):
        await entry.session.scrape("http://127.0.0.1/")
    assert entry.meter.snapshot()["pages"] == 0


async def test_forgetting_a_user_drops_the_live_session(make_registry):
    registry = make_registry()
    await registry.get("alice")
    registry.forget("alice")
    assert len(registry) == 0
    registry.forget("nobody")  # harmless


def test_run_slots_refuse_work_beyond_the_cap_and_free_up_afterwards():
    slots = RunSlots(2)
    with slots.hold(), slots.hold():
        with pytest.raises(TooBusy):
            with slots.hold():
                pass
        assert slots.active == 2
    assert slots.active == 0
    with slots.hold():
        assert slots.active == 1


def test_a_run_slot_is_released_when_the_work_fails():
    slots = RunSlots(1)
    with pytest.raises(RuntimeError):
        with slots.hold():
            raise RuntimeError("boom")
    assert slots.active == 0


def test_the_rate_limiter_allows_a_burst_up_to_the_limit_then_says_when_to_retry():
    clock = Clock()
    limiter = RateLimiter(3, clock=clock)
    for _ in range(3):
        limiter.check("alice")
    with pytest.raises(RateLimited) as caught:
        limiter.check("alice")
    assert 1 <= caught.value.retry_after <= 61
    limiter.check("bob")  # someone else is unaffected
    clock.now += 61
    limiter.check("alice")  # the window has slid past the old calls
