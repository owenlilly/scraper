import pytest

from scraper.browser import Browser
from scraper.config import Config
from scraper.fetch import FetchError, Page
from scraper.hosted import make_hosted_browser
from scraper.usage import MemoryUsageStore, UsageLimitReached, UsageMeter

URL = "https://site.test/search"
# Each tool, and how it is called: (the arguments after the address).
CALLS = {"submit_form": ({"q": "a"}, None), "click": ("Load more", 1), "scroll": (3,)}


def setup(check=None, base_result=None, **cfg):
    config = Config(hosted=True, **cfg)
    meter = UsageMeter("alice", MemoryUsageStore(), config)
    calls: list[dict] = []

    def make(kind):
        def tool(url, *args, timeout, allow_request, **kwargs):
            calls.append({"kind": kind, "url": url, "allow": allow_request})
            return [] if kind == "network" else (base_result or Page(url, "Results", "ok", html="<p>ok</p>"))

        return tool

    base = Browser(submit_form=make("submit_form"), click=make("click"), scroll=make("scroll"), network=make("network"))
    return make_hosted_browser(config, meter, base=base, check=check or (lambda url: None)), meter, calls


def use(browser, kind, url=URL, allow=lambda u, nav: True):
    args = CALLS.get(kind, ())
    return getattr(browser, kind)(url, *args, timeout=20.0, allow_request=allow)


ALL = [*CALLS, "network"]


@pytest.mark.parametrize("kind", ALL)
def test_a_private_address_is_refused_before_anything_is_opened_or_counted(kind):
    def check(url):
        raise FetchError("That address is not on the public internet")

    browser, meter, calls = setup(check=check)
    with pytest.raises(FetchError, match="public internet"):
        use(browser, kind, "http://10.0.0.5/")
    assert calls == [] and meter.snapshot()["pages"] == 0


@pytest.mark.parametrize("kind", ALL)
def test_each_use_costs_a_page_of_the_daily_allowance(kind):
    browser, meter, _ = setup()
    use(browser, kind)
    assert meter.snapshot()["pages"] == 1


@pytest.mark.parametrize("kind", ALL)
def test_it_stops_when_the_daily_allowance_is_spent(kind):
    browser, _, calls = setup(daily_pages=1)
    use(browser, kind)
    with pytest.raises(UsageLimitReached):
        use(browser, kind)
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ALL)
def test_the_browser_may_load_only_public_addresses_and_only_where_the_tool_allows_navigation(kind):
    def check(url):
        if "10.0.0.5" in url:
            raise FetchError("private")

    browser, _, calls = setup(check=check)
    use(browser, kind, allow=lambda u, nav: not (nav and "blocked.test" in u))
    allow = calls[0]["allow"]
    assert allow("https://cdn.test/app.js", False) is True
    assert allow("http://10.0.0.5/admin", False) is False  # a page's own script may not reach the inside of the network
    assert allow("http://10.0.0.5/", True) is False
    assert allow("https://blocked.test/", True) is False  # the tool's own rule still applies


@pytest.mark.parametrize("kind", [k for k in ALL if k != "network"])
def test_an_oversize_result_is_cut_before_it_is_kept(kind):
    big = "x" * 5000
    browser, _, _ = setup(base_result=Page(URL, "t", big, html=big), max_page_chars=1000)
    page = use(browser, kind)
    assert len(page.markdown) <= 1000 and len(page.html) <= 1000
