import http.server
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scraper.fetch import FetchError, Page, fetch, parse_html, validate_url

HOME = (
    "<html><head><title>Home Page</title></head><body>"
    '<nav><a href="/nav-only">Nav</a></nav>'
    "<h1>Welcome</h1><p>Some <b>bold</b> text. " + "filler " * 60 + "</p>"
    '<a href="/about#team">About</a> <a href="https://other.example/x">Out</a>'
    '<a href="mailto:a@b.c">mail</a> <a href="/about">About again</a>'
    "<script>var secret = 1;</script><footer>FOOTER</footer></body></html>"
)
TINY = "<html><head><title>T</title></head><body><div id='app'></div><script>boot()</script></body></html>"

ROUTES = {
    "/": (200, "text/html; charset=utf-8", HOME.encode()),
    "/tiny": (200, "text/html", TINY.encode()),
    "/pdf": (200, "application/pdf", b"%PDF-1.4 binary"),
    "/forbidden": (403, "text/html", b"no"),
    "/boom": (500, "text/html", b"err"),
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        status, ctype, body = ROUTES.get(self.path, (404, "text/html", b"<h1>nope</h1>"))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def fake_response(html="<html><body>ok</body></html>", status=200, ctype="text/html", url="https://x.test/"):
    return SimpleNamespace(status=status, url=url, html_content=html, headers={"Content-Type": ctype})


# ---- validate_url (Review Focus 1) ----------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("example.com", "https://example.com"),
        ("  https://example.com/a?b=1  ", "https://example.com/a?b=1"),
        ("http://localhost:8080/x", "http://localhost:8080/x"),
        ("localhost:8080/x", "http://localhost:8080/x"),
        ("127.0.0.1:8765/index.html", "http://127.0.0.1:8765/index.html"),
        ("https://localhost:8080/x", "https://localhost:8080/x"),
    ],
)
def test_validate_url_accepts_and_normalises(raw, expected):
    assert validate_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "javascript:alert(1)", "file:///etc/passwd", "ftp://example.com/a", "mailto:a@b.c", "http://", "https://exa mple.com"],
)
def test_validate_url_rejects(raw):
    with pytest.raises(FetchError):
        validate_url(raw)


# ---- parse_html -------------------------------------------------------------


def test_parse_html_cleans_noise_and_keeps_structure():
    page = parse_html(HOME, "https://s.test/")
    assert page.title == "Home Page"
    assert "# Welcome" in page.markdown
    assert "**bold**" in page.markdown
    assert "secret" not in page.markdown
    assert "FOOTER" not in page.markdown
    assert "Nav" not in page.markdown


def test_parse_html_links_are_absolute_deduped_and_web_only():
    page = parse_html(HOME, "https://s.test/")
    assert page.links == (
        "https://s.test/nav-only",
        "https://s.test/about",
        "https://other.example/x",
    )


def test_parse_html_title_falls_back_to_url():
    page = parse_html("<html><body><p>hi</p></body></html>", "https://s.test/p")
    assert page.title == "https://s.test/p"


# ---- fetch ------------------------------------------------------------------


def test_fetch_fast_against_local_server(site):
    page = fetch(site + "/", "fast", timeout=10)
    assert isinstance(page, Page)
    assert page.title == "Home Page"
    assert page.looks_empty is False
    assert page.url.startswith(site)


def test_fetch_flags_js_shell_as_empty(site):
    assert fetch(site + "/tiny", "fast", timeout=10).looks_empty is True


def test_fetch_http_errors(site):
    with pytest.raises(FetchError, match="404"):
        fetch(site + "/missing", "fast", timeout=10)
    with pytest.raises(FetchError) as forbidden:
        fetch(site + "/forbidden", "fast", timeout=10)
    assert forbidden.value.retry_mode == "stealthy"
    assert "Stealthy" in forbidden.value.hint


def test_fetch_rejects_non_html(site):
    # Review Focus 2
    with pytest.raises(FetchError, match="not an HTML page"):
        fetch(site + "/pdf", "fast", timeout=10)


def test_fetch_wraps_backend_exceptions():
    def boom(url, timeout):
        raise ConnectionError("Failed to connect to host")

    with pytest.raises(FetchError, match="Could not fetch"):
        fetch("https://x.test/", "fast", backends={"fast": boom})


def test_missing_browser_gives_install_hint():
    def no_browser(url, timeout):
        raise Exception("BrowserType.launch: Executable doesn't exist at /x/chromium")

    with pytest.raises(FetchError) as err:
        fetch("https://x.test/", "dynamic", backends={"dynamic": no_browser})
    assert "scrapling install" in err.value.hint


def test_empty_flag_only_applies_to_fast_mode():
    shell = lambda url, timeout: fake_response(TINY)
    assert fetch("https://x.test/", "dynamic", backends={"dynamic": shell}).looks_empty is False
    assert fetch("https://x.test/", "fast", backends={"fast": shell}).looks_empty is True


def test_unknown_mode_is_a_programming_error():
    with pytest.raises(ValueError):
        fetch("https://x.test/", "turbo")


def test_backend_error_messages_drop_library_boilerplate():
    def refused(url, timeout):
        raise Exception(
            "Failed to perform, curl: (7) Failed to connect to 127.0.0.1:1 after 0 ms: "
            "Could not connect to server. See https://curl.se/libcurl/c/libcurl-errors.html first for more details."
        )

    with pytest.raises(FetchError) as err:
        fetch("https://x.test/", "fast", backends={"fast": refused})
    assert "curl.se" not in err.value.message
    assert "Could not connect to server" in err.value.message
    assert "Failed to perform, curl: (7)" not in err.value.message


def test_parse_html_keeps_raw_html_for_selectors_without_polluting_repr_or_equality():
    page = parse_html(HOME, "https://s.test/")
    assert "<h1>Welcome</h1>" in page.html
    assert "html=" not in repr(page)
    assert page == replace(page, html="something else")


def test_config_fetch_modes_match_the_fetcher():
    from scraper.config import FETCH_MODES
    from scraper.fetch import MODES

    assert FETCH_MODES == MODES


