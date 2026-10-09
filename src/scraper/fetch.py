"""Scrapling wrapper: URL + mode -> cleaned markdown Page."""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify
from scrapling.parser import Selector

Mode = Literal["fast", "dynamic", "stealthy"]
MODES = ("fast", "dynamic", "stealthy")
NOISE_TAGS = ["script", "style", "noscript", "svg", "nav", "footer", "aside", "iframe", "form"]
EMPTY_THRESHOLD = 200
BROWSER_MISSING = ("Executable doesn't exist", "playwright install", "scrapling install")
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    markdown: str
    links: tuple[str, ...] = ()
    looks_empty: bool = False


class FetchError(Exception):
    def __init__(self, message: str, *, hint: str | None = None, retry_mode: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.retry_mode = retry_mode


def validate_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url or re.search(r"\s", url):
        raise FetchError("Enter a valid URL")
    if "://" not in url:
        match = _SCHEME.match(url)
        if match and not url[match.end() : match.end() + 1].isdigit():
            raise FetchError("Only http and https URLs are supported")
        url = "https://" + url
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("Only http and https URLs are supported")
    try:
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise FetchError("Enter a valid URL") from None
    if not host:
        raise FetchError("Enter a valid URL")
    return url


def _extract_links(hrefs: list[str], base: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for href in hrefs:
        absolute, _ = urldefrag(urljoin(base, str(href).strip()))
        if urlparse(absolute).scheme in ("http", "https"):
            seen.setdefault(absolute)
    return tuple(seen)


def parse_html(html: str, url: str) -> Page:
    selector = Selector(html, url=url)
    title = (selector.css("title::text").get() or "").strip() or url
    links = _extract_links(selector.css("a::attr(href)").getall(), url)
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    markdown = re.sub(r"\n{3,}", "\n\n", markdownify(str(soup), heading_style="ATX")).strip()
    return Page(url=url, title=title, markdown=markdown, links=links)


def _fast(url: str, timeout: float):
    from scrapling.fetchers import Fetcher

    return Fetcher.get(url, timeout=timeout, retries=2)


def _dynamic(url: str, timeout: float):
    from scrapling.fetchers import DynamicFetcher

    return DynamicFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout * 1000)


def _stealthy(url: str, timeout: float):
    from scrapling.fetchers import StealthyFetcher

    return StealthyFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout * 1000)


DEFAULT_BACKENDS: dict[str, Callable] = {"fast": _fast, "dynamic": _dynamic, "stealthy": _stealthy}


def _http_error(status: int, url: str) -> FetchError:
    hint, retry = None, None
    if status in (401, 403, 429):
        hint, retry = "The site may be blocking simple requests. Try Stealthy mode.", "stealthy"
    elif status >= 500:
        hint = "The server reported an error. Try again later."
    return FetchError(f"HTTP {status} from {url}", hint=hint, retry_mode=retry)


def _translate(exc: Exception, mode: str, url: str) -> FetchError:
    text = str(exc)
    if mode != "fast" and any(marker in text for marker in BROWSER_MISSING):
        return FetchError(
            "The browser for this mode is not installed",
            hint="Run `uv run scrapling install` once, then try again.",
        )
    first_line = text.splitlines()[0][:200] if text else type(exc).__name__
    return FetchError(f"Could not fetch {url}: {first_line}")


def _content_type(response) -> str:
    headers = getattr(response, "headers", None) or {}
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    return lowered.get("content-type", "").lower()


def fetch(url: str, mode: str = "fast", *, timeout: float = 20.0, backends: dict | None = None) -> Page:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    url = validate_url(url)
    backend = (backends or DEFAULT_BACKENDS)[mode]
    try:
        response = backend(url, timeout)
    except Exception as exc:
        raise _translate(exc, mode, url) from exc
    status = int(response.status)
    if status >= 400:
        raise _http_error(status, url)
    ctype = _content_type(response)
    if ctype and not any(token in ctype for token in ("html", "xml", "text/plain")):
        raise FetchError(f"{url} is not an HTML page ({ctype.split(';')[0]})")
    final_url = str(getattr(response, "url", None) or url)
    page = parse_html(str(response.html_content), final_url)
    return replace(page, looks_empty=(mode == "fast" and len(page.markdown) < EMPTY_THRESHOLD))
