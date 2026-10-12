"""The browser tools in a real browser, against a small web site served from this machine. Skipped when the browsers are not installed."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

from scraper.browser import click_element, network_requests, scroll_page, submit_form
from scraper.fetch import FetchError

FORM = (
    b"<html><head><title>Search</title></head><body>"
    b'<form method="post" action="%s"><input name="q" id="q">'
    b'<select name="kind"><option value="a">A</option><option value="b">B</option></select>'
    b'<input type="checkbox" name="exact"><button type="submit" id="go">Search</button></form></body></html>'
)


LIST_PAGE = b"""<html><head><title>List</title></head><body><ul id=list><li>alpha</li><li>beta</li></ul>
<button id=more onclick="fetch('/more').then(r => r.json()).then(d => { d.items.forEach(i => { const li = document.createElement('li'); li.textContent = i; list.appendChild(li); }); more.remove(); })">Load more</button>
<a id=next href="/plain">Next page</a><script>fetch('/api/items?page=1').then(r => r.json())</script></body></html>"""
INFINITE_PAGE = b"""<html><head><title>Infinite</title></head><body><div style="height: 3000px">top</div><ul id=feed><li>first</li></ul>
<script>let loaded = false; window.addEventListener('scroll', () => { if (!loaded && window.scrollY > 500) { loaded = true; feed.insertAdjacentHTML('beforeend', '<li>lazy item</li>'); } });</script></body></html>"""


class Site(BaseHTTPRequestHandler):
    other = ""  # where the "away" form sends its data

    def _send(self, body: bytes):
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/more":
            self._send_json(b'{"items": ["gamma", "delta"], "next": null}')
        elif path == "/api/items":
            self._send_json(b'{"items": ["alpha", "beta"]}')
        elif path == "/list":
            self._send(LIST_PAGE)
        elif path == "/infinite":
            self._send(INFINITE_PAGE)
        elif path == "/plain":
            self._send(b"<html><head><title>Plain</title></head><body><p>nothing dynamic</p></body></html>")
        elif path == "/json":
            self._send_json(b'{"hello": "world"}')
        else:
            self._send(FORM % (self.other.encode() + b"/results" if path == "/away" else b"/results"))

    def _send_json(self, body: bytes):
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = parse_qs(self.rfile.read(int(self.headers["content-length"])).decode())
        shown = ", ".join(f"{k}={v[0]}" for k, v in sorted(data.items()))
        self._send(f"<html><head><title>Results</title></head><body><h1>Got {shown}</h1></body></html>".encode())

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    server = HTTPServer(("127.0.0.1", 0), Site)
    port = server.server_address[1]
    Site.other = f"http://localhost:{port}"  # the same server under a second host name
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def submit(url, fields, submit_=None, allow=lambda u, nav: True):
    try:
        return submit_form(url, fields, submit_, timeout=20.0, allow_request=allow)
    except FetchError as exc:
        if "not installed" in exc.message:
            pytest.skip("the browser for this mode is not installed (uv run scrapling install)")
        raise


def test_it_fills_a_field_and_clicks_the_button_named_by_a_selector(site):
    page = submit(site + "/", {"q": "cheap plans"}, "#go")
    assert page.url == site + "/results" and "q=cheap plans" in page.markdown and page.title == "Results"


def test_it_finds_fields_by_name_or_by_selector_and_the_button_by_its_text(site):
    page = submit(site + "/", {"#q": "x"}, "Search")
    assert "q=x" in page.markdown


def test_it_presses_enter_when_no_button_is_named(site):
    assert "q=hello" in submit(site + "/", {"q": "hello"}).markdown


def test_it_sets_a_menu_and_a_checkbox(site):
    page = submit(site + "/", {"q": "z", "kind": "b", "exact": True}, "#go")
    assert "exact=on" in page.markdown and "kind=b" in page.markdown
    assert "exact" not in submit(site + "/", {"q": "z", "exact": False}, "#go").markdown


def test_a_field_that_is_not_on_the_page_is_named_in_the_error(site):
    with pytest.raises(FetchError, match="nothere"):
        submit(site + "/", {"nothere": "x"}, "#go")


def test_a_button_that_is_not_on_the_page_is_named_in_the_error(site):
    with pytest.raises(FetchError, match="Nope"):
        submit(site + "/", {"q": "x"}, "Nope")


def test_every_request_is_put_to_the_guard_and_a_refusal_stops_the_form_going_away(site):
    asked: list[tuple[str, bool]] = []

    def allow(url, navigation):
        asked.append((url, navigation))
        return not (navigation and "localhost" in url)  # the form below posts to a second host name

    with pytest.raises(FetchError, match="localhost"):
        submit(site + "/away", {"q": "x"}, "#go", allow)
    assert (site + "/away", True) in asked and any("localhost" in url and nav for url, nav in asked)


# ---- click -----------------------------------------------------------------------------------------------------------------------------


def click(url, target, times=1, allow=lambda u, nav: True):
    try:
        return click_element(url, target, times, timeout=20.0, allow_request=allow)
    except FetchError as exc:
        if "not installed" in exc.message:
            pytest.skip("the browser for this mode is not installed (uv run scrapling install)")
        raise


def test_it_clicks_a_button_by_its_text_and_returns_what_the_click_loaded(site):
    page = click(site + "/list", "Load more")
    assert "gamma" in page.markdown and "delta" in page.markdown and "alpha" in page.markdown


def test_it_clicks_by_selector(site):
    assert "gamma" in click(site + "/list", "#more").markdown


def test_it_follows_a_link_it_clicks(site):
    page = click(site + "/list", "Next page")
    assert page.url == site + "/plain" and "nothing dynamic" in page.markdown


def test_it_stops_clicking_when_the_thing_it_clicks_is_gone(site):
    assert "gamma" in click(site + "/list", "Load more", times=5).markdown  # the button removes itself after one click


def test_an_element_that_is_not_there_is_named_in_the_error(site):
    with pytest.raises(FetchError, match="Nothing to press"):
        click(site + "/list", "Nothing to press")


# ---- scroll ----------------------------------------------------------------------------------------------------------------------------


def scroll(url, times=3, allow=lambda u, nav: True):
    try:
        return scroll_page(url, times, timeout=20.0, allow_request=allow)
    except FetchError as exc:
        if "not installed" in exc.message:
            pytest.skip("the browser for this mode is not installed (uv run scrapling install)")
        raise


def test_scrolling_loads_what_only_appears_on_scroll(site):
    assert "lazy item" in scroll(site + "/infinite").markdown


def test_a_page_that_does_not_grow_is_scrolled_once_and_returned(site):
    assert "nothing dynamic" in scroll(site + "/plain", times=10).markdown


# ---- network ---------------------------------------------------------------------------------------------------------------------------


def network(url, **kw):
    try:
        return network_requests(url, timeout=20.0, allow_request=kw.pop("allow", lambda u, nav: True), **kw)
    except FetchError as exc:
        if "not installed" in exc.message:
            pytest.skip("the browser for this mode is not installed (uv run scrapling install)")
        raise


def test_it_lists_the_background_requests_a_page_makes_with_a_peek_at_json(site):
    requests = network(site + "/list")
    api = next(r for r in requests if r.url.endswith("/api/items?page=1"))
    assert api.method == "GET" and api.status == 200 and "json" in api.content_type
    assert '"alpha"' in api.preview and api.size > 0


def test_it_can_filter_the_requests_by_a_word_in_the_address(site):
    assert [r.url for r in network(site + "/list", contains="items")] == [site + "/api/items?page=1"]
    assert network(site + "/list", contains="nonexistent") == []


def test_a_page_that_makes_no_background_requests_gives_an_empty_list(site):
    assert network(site + "/plain") == []


def test_the_guard_is_asked_about_background_requests_too(site):
    seen: list[str] = []

    def allow(url, navigation):
        seen.append(url)
        return True

    network(site + "/list", allow=allow)
    assert any(url.endswith("/api/items?page=1") for url in seen)


def test_a_request_the_guard_refuses_is_written_to_the_debug_log(site, tmp_path):
    import json

    from scraper import debuglog
    from scraper.config import Config

    debuglog.configure(Config(debug=True, debug_dir=str(tmp_path)))
    with pytest.raises(FetchError):
        submit(site + "/away", {"q": "x"}, "#go", lambda url, navigation: not (navigation and "localhost" in url))
    entries = [json.loads(line) for f in tmp_path.glob("debug-*.jsonl") for line in f.read_text().splitlines()]
    blocked = [e for e in entries if e["event"] == "browser.blocked"]
    assert blocked and blocked[0]["action"] == "browser_submit_form" and "localhost" in blocked[0]["url"] and blocked[0]["navigation"] is True
    assert any(e["event"] == "browser.error" for e in entries)
