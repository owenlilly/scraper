import pytest

from fakes import make_fetch, make_page
from scraper.browser import Browser, NetworkRequest
from scraper.fetch import FetchError, parse_html
from scraper.tools import ToolBox
from scraper.tools.pages import describe_forms

START = "https://site.test/search"
RESULT = parse_html("<html><head><title>Results</title></head><body><h1>3 results</h1><p>Starter $10</p></body></html>", "https://site.test/results?q=plans")

# What each page-returning tool is called with, in the arguments the model gives it.
PAGE_TOOLS = {
    "browser_submit_form": {"url": START, "fields": {"q": "plans"}, "submit": "Search"},
    "browser_click": {"url": START, "click": "Load more", "times": 2},
    "browser_scroll": {"url": START, "times": 4},
}
ALL_TOOLS = {**PAGE_TOOLS, "browser_network": {"url": START}}


def browser_box(result=RESULT, hosts=("site.test",), requests=(), **kw):
    """A ToolBox whose browser is a recorder: every call is noted, and each tool returns `result` (or raises it, if it is an error)."""
    seen: list[dict] = []

    def make(kind):
        def call(url, *args, timeout, allow_request, **kwargs):
            seen.append({"kind": kind, "url": url, "args": args, "kwargs": kwargs, "timeout": timeout, "allow": allow_request})
            if isinstance(result, Exception):
                raise result
            return list(requests) if kind == "network" else result

        return call

    browser = Browser(submit_form=make("submit_form"), click=make("click"), scroll=make("scroll"), network=make("network"))
    pages: list = []
    box = ToolBox(pages=pages, rows=[], fetch_fn=make_fetch({}, []), browser=browser, allowed_hosts=set(hosts), **kw)
    return box, pages, seen


# ---- the three that return a page ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", PAGE_TOOLS)
def test_it_returns_the_page_that_comes_back_and_keeps_it_for_the_other_tools(name):
    box, pages, seen = browser_box()
    result = box.execute(name, PAGE_TOOLS[name])
    assert not result.is_error and "3 results" in result.text and "https://site.test/results?q=plans" in result.text
    assert result.fetched == ("https://site.test/results?q=plans",) and pages == [RESULT] and len(seen) == 1


def test_the_form_tool_passes_on_the_fields_and_the_button():
    box, _, seen = browser_box()
    result = box.execute("browser_submit_form", PAGE_TOOLS["browser_submit_form"])
    assert "Submitted the form on https://site.test/search" in result.text
    assert seen[0]["args"] == ({"q": "plans"}, "Search")


def test_the_form_tools_button_is_optional():
    box, _, seen = browser_box()
    box.execute("browser_submit_form", {"url": START, "fields": {"q": "plans"}})
    assert seen[0]["args"] == ({"q": "plans"}, None)


def test_the_click_tool_passes_on_what_to_click_and_how_often():
    box, _, seen = browser_box()
    result = box.execute("browser_click", PAGE_TOOLS["browser_click"])
    assert seen[0]["args"] == ("Load more", 2) and "Clicked 'Load more'" in result.text


def test_the_click_tool_clicks_once_unless_told_more_and_never_more_than_ten_times():
    box, _, seen = browser_box()
    box.execute("browser_click", {"url": START, "click": "Next"})
    box.execute("browser_click", {"url": START, "click": "Next", "times": 99})
    assert [call["args"][1] for call in seen] == [1, 10]


def test_the_scroll_tool_scrolls_three_times_unless_told_otherwise_and_never_more_than_twenty():
    box, _, seen = browser_box()
    result = box.execute("browser_scroll", {"url": START})
    box.execute("browser_scroll", {"url": START, "times": 99})
    assert [call["args"][0] for call in seen] == [3, 20] and "Scrolled" in result.text


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_each_use_costs_a_page_of_the_budget(name):
    box, _, seen = browser_box(max_pages=1)
    assert not box.execute(name, ALL_TOOLS[name]).is_error
    second = box.execute(name, ALL_TOOLS[name])
    assert second.is_error and second.limit == "pages" and len(seen) == 1


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_it_waits_out_the_fetch_delay_like_a_fetch_does(name):
    slept: list[float] = []
    box, _, _ = browser_box(min_delay=2.0, delay=2.0, clock=lambda: 100.0, sleep=slept.append)
    box.execute(name, ALL_TOOLS[name])
    box.execute(name, ALL_TOOLS[name])
    assert slept == [2.0]


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_it_will_not_open_a_site_the_person_did_not_name(name):
    box, _, seen = browser_box()
    result = box.execute(name, {**ALL_TOOLS[name], "url": "https://elsewhere.test/search"})
    assert result.is_error and "elsewhere.test is not allowed" in result.text and seen == []


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_it_asks_for_approval_of_a_new_site_the_way_fetch_page_does(name):
    box, _, seen = browser_box()
    box.approve_host = lambda host: host == "elsewhere.test"
    assert not box.execute(name, {**ALL_TOOLS[name], "url": "https://elsewhere.test/search"}).is_error
    assert "elsewhere.test" in box.allowed_hosts and len(seen) == 1


@pytest.mark.parametrize("name", PAGE_TOOLS)
def test_a_result_on_a_site_that_is_not_allowed_is_discarded(name):
    other = parse_html("<html><body>login</body></html>", "https://login.other.test/")
    box, pages, _ = browser_box(result=other)
    result = box.execute(name, PAGE_TOOLS[name])
    assert result.is_error and "login.other.test" in result.text and "discarded" in result.text and pages == []


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_a_failure_in_the_browser_is_reported_with_its_hint(name):
    box, _, _ = browser_box(result=FetchError("Could not find the element 'x' on the page.", hint="Use the text on the button."))
    result = box.execute(name, ALL_TOOLS[name])
    assert result.is_error and "Could not find the element 'x'" in result.text and "Use the text on the button." in result.text


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_the_browser_may_only_navigate_to_sites_that_are_allowed_now(name):
    box, _, seen = browser_box()
    box.execute(name, ALL_TOOLS[name])
    allow = seen[0]["allow"]
    assert allow("https://site.test/results", True) is True
    assert allow("https://evil.test/", True) is False  # a page that sends the browser to another site
    assert allow("https://cdn.other.test/app.js", False) is True  # but pages may load their own scripts and images
    box.allowed_hosts.add("evil.test")
    assert allow("https://evil.test/", True) is True


@pytest.mark.parametrize("name", PAGE_TOOLS)
def test_it_does_not_cache_the_way_fetch_page_does(name):
    box, _, seen = browser_box()
    for _ in range(2):
        box.execute(name, PAGE_TOOLS[name])
    assert len(seen) == 2


@pytest.mark.parametrize(
    "name, args, message",
    [
        ("browser_submit_form", {"fields": {"q": "a"}}, "Missing required argument 'url'"),
        ("browser_submit_form", {"url": START}, "fields"),
        ("browser_submit_form", {"url": START, "fields": {}}, "fields"),
        ("browser_submit_form", {"url": START, "fields": ["q"]}, "fields"),
        ("browser_submit_form", {"url": START, "fields": {"q": ["a"]}}, "'q'"),
        ("browser_submit_form", {"url": START, "fields": {f"f{i}": "x" for i in range(21)}}, "at most 20"),
        ("browser_submit_form", {"url": START, "fields": {"q": "a"}, "submit": 5}, "'submit' must be a string"),
        ("browser_click", {"url": START}, "Missing required argument 'click'"),
        ("browser_click", {"click": "x"}, "Missing required argument 'url'"),
        ("browser_click", {"url": START, "click": "x", "times": "lots"}, "'times' must be an integer"),
        ("browser_scroll", {}, "Missing required argument 'url'"),
        ("browser_scroll", {"url": START, "times": 1.5}, "'times' must be an integer"),
        ("browser_network", {}, "Missing required argument 'url'"),
        ("browser_network", {"url": START, "contains": 4}, "'contains' must be a string"),
    ],
)
def test_it_says_what_is_wrong_with_the_arguments(name, args, message):
    box, _, seen = browser_box()
    result = box.execute(name, args)
    assert result.is_error and message in result.text and seen == []


# ---- network ------------------------------------------------------------------------------------------------------------------------------

API = NetworkRequest("GET", "https://site.test/api/items?page=1", 200, "application/json", 1234, '{"items": ["alpha"]}')
BEACON = NetworkRequest("POST", "https://site.test/track", 204, "", 0, "")


def test_the_network_tool_lists_each_request_with_its_status_and_size_and_a_peek_at_json():
    box, pages, seen = browser_box(requests=[API, BEACON])
    result = box.execute("browser_network", {"url": START})
    assert not result.is_error and pages == []  # it lists requests; it does not store a page
    assert "GET 200 application/json 1.2 KB https://site.test/api/items?page=1" in result.text
    assert '{"items": ["alpha"]}' in result.text and "POST 204" in result.text
    assert seen[0]["kwargs"] == {"contains": None, "limit": 30}


def test_the_network_tool_passes_on_its_filter_and_limit():
    box, _, seen = browser_box(requests=[API])
    box.execute("browser_network", {"url": START, "contains": "api", "limit": 5})
    assert seen[0]["kwargs"] == {"contains": "api", "limit": 5}


def test_the_network_tool_says_so_when_the_page_makes_no_background_requests():
    box, _, _ = browser_box(requests=[])
    result = box.execute("browser_network", {"url": START})
    assert not result.is_error and "no background requests" in result.text


# ---- the forms a fetched page offers ----------------------------------------------------------------------------

FORM_PAGE = parse_html(
    """<html><head><title>Search</title></head><body><h1>Find</h1>
    <form id="search" method="post" action="/results">
      <input name="q" type="text"><input name="csrf" type="hidden" value="x">
      <select name="kind"><option>a</option></select><input name="exact" type="checkbox">
      <button id="go" type="submit">Search</button>
    </form><form action="/newsletter"><input name="email"><input type="submit" value="Subscribe"></form></body></html>""",
    START,
)


def test_a_fetched_page_lists_the_forms_it_has_and_their_fields():
    text = describe_forms(FORM_PAGE)
    assert "#search" in text and "POST /results" in text
    for field in ("q", "kind", "exact"):
        assert field in text
    assert "csrf" not in text  # hidden fields are filled by the page itself
    assert '"Search"' in text and '"Subscribe"' in text and "email" in text


def test_a_page_without_forms_lists_none():
    assert describe_forms(make_page("https://site.test/")) == ""


def test_the_fetch_result_tells_the_model_about_the_forms():
    box, pages, _ = browser_box()
    box.fetch_fn = lambda url, mode, timeout=20.0: FORM_PAGE
    result = box.execute("fetch_page", {"url": START})
    assert "browser_submit_form" in result.text and "#search" in result.text


def test_it_lists_at_most_five_forms():
    html = "<html><body>" + "".join(f'<form id="f{i}"><input name="n{i}"></form>' for i in range(8)) + "</body></html>"
    text = describe_forms(parse_html(html, START))
    assert "#f4" in text and "#f5" not in text
