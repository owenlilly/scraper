import pytest

from fakes import make_page
from scraper.config import Config
from scraper.fetch import FetchError
from scraper.hosted import make_hosted_fetch
from scraper.usage import MemoryUsageStore, UsageLimitReached, UsageMeter

CFG = Config(hosted=True, daily_pages=2)


class Spy:
    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = [], result or make_page("https://example.test/"), error

    def __call__(self, url, mode="fast", *, timeout=20.0):
        self.calls.append((url, mode, timeout))
        if self.error:
            raise self.error
        return self.result


def build(base=None, check=None, cfg=CFG):
    meter = UsageMeter("u1", MemoryUsageStore(), cfg)
    checks = []
    return make_hosted_fetch(cfg, meter, base=base or Spy(), check=check or checks.append), meter, checks


def test_a_valid_fetch_is_checked_charged_and_passed_on():
    base = Spy()
    fetch_fn, meter, checks = build(base)
    page = fetch_fn("example.test/a", "fast", timeout=7.0)
    assert page == base.result  # equal, not identical: the page is copied when it is cut to the cap
    assert checks == ["https://example.test/a"]  # the scheme is added before the address check
    assert base.calls == [("https://example.test/a", "fast", 7.0)]
    assert meter.snapshot()["pages"] == 1


def test_a_mode_this_server_does_not_offer_is_refused_before_anything_else():
    base = Spy()
    fetch_fn, meter, checks = build(base)
    with pytest.raises(FetchError, match="dynamic mode is not available.*fast"):
        fetch_fn("https://example.test/", "dynamic")
    assert base.calls == [] and checks == [] and meter.snapshot()["pages"] == 0


def test_widened_modes_are_honoured():
    base = Spy()
    fetch_fn, _, _ = build(base, cfg=Config(hosted=True, fetch_modes="fast,dynamic"))
    fetch_fn("https://example.test/", "dynamic")
    assert base.calls[0][1] == "dynamic"


def test_a_non_public_address_is_refused_and_costs_nothing():
    base = Spy()

    def refuse(url):
        raise FetchError("localhost is not a public address, so it cannot be fetched")

    fetch_fn, meter, _ = build(base, check=refuse)
    with pytest.raises(FetchError, match="not a public address"):
        fetch_fn("http://localhost/")
    assert base.calls == [] and meter.snapshot()["pages"] == 0


def test_an_invalid_url_is_refused_before_the_address_check():
    base = Spy()
    fetch_fn, _, checks = build(base)
    with pytest.raises(FetchError, match="valid URL"):
        fetch_fn("not a url")
    assert checks == [] and base.calls == []


def test_the_daily_page_limit_raises_usage_limit_not_a_fetch_error():
    base = Spy()
    fetch_fn, _, _ = build(base)
    fetch_fn("https://example.test/1")
    fetch_fn("https://example.test/2")
    with pytest.raises(UsageLimitReached):
        fetch_fn("https://example.test/3")
    assert len(base.calls) == 2


def test_a_retry_suggestion_for_a_mode_that_is_off_is_dropped():
    blocked = FetchError("HTTP 403 from https://example.test/", hint="The site may be blocking simple requests. Try Stealthy mode.", retry_mode="stealthy")
    fetch_fn, _, _ = build(Spy(error=blocked))
    with pytest.raises(FetchError) as caught:
        fetch_fn("https://example.test/")
    assert caught.value.retry_mode is None and caught.value.hint is None
    assert caught.value.message == "HTTP 403 from https://example.test/"


def test_a_retry_suggestion_for_an_available_mode_is_kept():
    blocked = FetchError("HTTP 403", hint="Try Stealthy mode.", retry_mode="stealthy")
    fetch_fn, _, _ = build(Spy(error=blocked), cfg=Config(hosted=True, fetch_modes="fast,stealthy"))
    with pytest.raises(FetchError) as caught:
        fetch_fn("https://example.test/")
    assert caught.value.retry_mode == "stealthy"


# ---- a page is cut to the cap before the session keeps it --------------------------------------------------------------


def test_a_page_longer_than_the_cap_is_cut_before_the_session_keeps_it():
    big = make_page("https://example.test/", title="Big", markdown="m" * 1000, links=["https://example.test/next"], html="h" * 1000)
    fetch_fn, _, _ = build(Spy(result=big), cfg=Config(hosted=True, max_page_chars=200))
    page = fetch_fn("https://example.test/")
    assert len(page.markdown) == 200 and len(page.html) == 200
    assert page.markdown.endswith("[The rest of this page was cut off to save space.]")  # the model is told it is reading a part
    assert (page.url, page.title, page.links) == (big.url, "Big", big.links)


def test_a_page_within_the_cap_is_returned_unchanged():
    small = make_page("https://example.test/", markdown="short", html="<p>short</p>")
    fetch_fn, _, _ = build(Spy(result=small), cfg=Config(hosted=True, max_page_chars=200))
    assert fetch_fn("https://example.test/") == small


def test_a_cap_smaller_than_the_notice_still_never_exceeds_the_cap():
    fetch_fn, _, _ = build(Spy(result=make_page("https://example.test/", markdown="m" * 500, html="h" * 500)), cfg=Config(hosted=True, max_page_chars=10))
    page = fetch_fn("https://example.test/")
    assert len(page.markdown) <= 10 and len(page.html) <= 10
