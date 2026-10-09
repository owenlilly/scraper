"""Agent tools: the ToolBox logic (allowlist, budgets, idempotent fetch) and LangChain tool wrappers."""

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from langchain_core.tools import tool
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt
from scrapling.parser import Selector

from .context import estimate_tokens, view_page
from .crawl import normalize
from .fetch import MODES, FetchError, Page, validate_url

MODE_ALIASES = {
    "stealth": "stealthy",
    "browser": "dynamic",
    "js": "dynamic",
    "javascript": "dynamic",
    "http": "fast",
    "static": "fast",
}
MAX_ROWS_PER_CALL = 200
MAX_LINKS = 200
MAX_FETCH_DELAY = 30.0
URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+", re.IGNORECASE)
BARE_RE = re.compile(
    r"(?<![\w@/.-])((?:[a-z0-9-]+\.)+[a-z]{2,24}|localhost|(?:\d{1,3}\.){3}\d{1,3})(?=\.?(?:[:/\s,;)\]\"']|$))",
    re.IGNORECASE,
)


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def hosts_in(text: str) -> set[str]:
    hosts = {host_of(match) for match in URL_RE.findall(text)}
    hosts |= {match.lower().removeprefix("www.") for match in BARE_RE.findall(text)}
    hosts.discard("")
    return hosts


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False
    rows_added: int = 0
    fetched: tuple[str, ...] = ()
    limit: str | None = None


class ToolArgError(Exception):
    pass


class ToolBox:
    def __init__(
        self,
        *,
        pages: list[Page],
        rows: list[dict],
        fetch_fn: Callable[..., Page],
        allowed_hosts: set[str],
        max_pages: int = 30,
        result_tokens: int = 3000,
        timeout: float = 20.0,
        fetched: int = 0,
        grant: int | None = None,
        min_delay: float = 0.0,
        max_delay: float = MAX_FETCH_DELAY,
        delay: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        approve_host: Callable[[str], bool] | None = None,
        on_page: Callable[[Page], None] | None = None,
        on_rows: Callable[[list[dict]], None] | None = None,
        on_delay: Callable[[float], None] | None = None,
    ) -> None:
        self.pages = pages
        self.rows = rows
        self.fetch_fn = fetch_fn
        self.allowed_hosts = set(allowed_hosts)
        self.max_pages = max_pages
        self.result_tokens = result_tokens
        self.timeout = timeout
        self.fetched = fetched
        self.approve_host = approve_host
        self.on_page = on_page
        self.on_rows = on_rows
        self.on_delay = on_delay
        self.min_delay = min_delay
        self.max_delay = max(min_delay, max_delay)
        self.delay = min(self.max_delay, max(min_delay, delay))  # the pause kept between two fetches
        self._clock = clock
        self._sleep = sleep
        self._last_fetch: float | None = None
        self._grant = grant if grant is not None else max_pages
        self._done: dict[tuple[str, str], Page] = {}
        self._handlers = {
            "fetch_page": self._fetch_page,
            "list_links": self._list_links,
            "read_page": self._read_page,
            "extract": self._extract,
            "add_rows": self._add_rows,
            "set_fetch_delay": self._set_fetch_delay,
        }

    def grant_pages(self) -> None:
        self.max_pages += self._grant

    # ---- entry point ---------------------------------------------------------

    def execute(self, name: str, args: dict) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return self._error(f"Unknown tool {name!r}. Available tools: {', '.join(self._handlers)}.")
        if not isinstance(args, dict):
            return self._error("Tool arguments must be a JSON object.")
        try:
            return handler(args)
        except GraphBubbleUp:
            raise  # a pause (interrupt) must reach LangGraph; it is not a tool error
        except ToolArgError as exc:
            return self._error(str(exc))
        except Exception as exc:  # a bug or surprise inside a tool must not end the run
            return self._error(f"{name} failed: {type(exc).__name__}: {exc}")

    # ---- helpers -------------------------------------------------------------

    def _cap(self, text: str) -> str:
        limit = self.result_tokens * 4
        return text if len(text) <= limit else text[:limit].rstrip() + "\n[output truncated]"

    def _ok(self, text: str, **kwargs) -> ToolResult:
        return ToolResult(self._cap(text), **kwargs)

    def _error(self, text: str, **kwargs) -> ToolResult:
        return ToolResult(self._cap(text), is_error=True, **kwargs)

    @staticmethod
    def _str(args: dict, key: str, *, required: bool = False) -> str | None:
        value = args.get(key)
        if value is None or value == "":
            if required:
                raise ToolArgError(f"Missing required argument '{key}'.")
            return None
        if not isinstance(value, str):
            raise ToolArgError(f"Argument '{key}' must be a string.")
        return value

    @staticmethod
    def _int(args: dict, key: str, default: int, low: int, high: int) -> int:
        value = args.get(key)
        if value is None:
            return default
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolArgError(f"Argument '{key}' must be an integer.")
        return max(low, min(high, value))

    @staticmethod
    def _seconds(args: dict, key: str) -> float:
        value = args.get(key)
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                pass
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ToolArgError(f"Argument '{key}' must be a number of seconds, zero or more.")
        return float(value)

    def _pace(self) -> None:
        """Wait out what is left of the delay since the previous request ended."""
        if self._last_fetch is not None and self.delay:
            remaining = self.delay - (self._clock() - self._last_fetch)
            if remaining > 0:
                self._sleep(remaining)

    @staticmethod
    def _mode(value) -> str:
        if value is None or value == "":
            return "fast"
        if not isinstance(value, str):
            raise ToolArgError("Argument 'mode' must be a string.")
        mode = MODE_ALIASES.get(value.strip().lower(), value.strip().lower())
        if mode not in MODES:
            raise ToolArgError(f"Unknown mode {value!r}. Use one of: {', '.join(MODES)}.")
        return mode

    def _stored_list(self) -> str:
        urls = [page.url for page in self.pages[:10]]
        return ", ".join(urls) if urls else "none"

    def _find(self, url: str) -> Page:
        key = normalize(url)
        for page in self.pages:
            if normalize(page.url) == key:
                return page
        raise ToolArgError(f"No stored page for {url}. Stored pages: {self._stored_list()}. Call fetch_page first.")

    def _store(self, page: Page) -> None:
        key = normalize(page.url)
        for index, existing in enumerate(self.pages):
            if normalize(existing.url) == key:
                self.pages[index] = page
                return
        self.pages.append(page)

    def _not_allowed(self, host: str) -> ToolResult:
        allowed = ", ".join(sorted(self.allowed_hosts)) or "none"
        return self._error(
            f"Fetching {host} is not allowed. Allowed sites: {allowed}. "
            "The user must name a site in their instruction or approve it before it can be fetched; say so in your answer if you need it."
        )

    def _page_result(self, page: Page, mode: str, focus: str | None) -> ToolResult:
        note = ""
        if page.looks_empty and mode == "fast":
            note = " The page looks nearly empty; it may need mode='dynamic'."
        header = f"Fetched {page.url} (mode {mode}): about {estimate_tokens(page.markdown)} tokens, {len(page.links)} links.{note}\n"
        body = view_page(page, focus, max(200, self.result_tokens - estimate_tokens(header) - 10))
        return self._ok(header + body, fetched=(page.url,))

    # ---- tools ---------------------------------------------------------------

    def _fetch_page(self, args: dict) -> ToolResult:
        url = self._str(args, "url", required=True)
        mode = self._mode(args.get("mode"))
        focus = self._str(args, "focus")
        try:
            url = validate_url(url)
        except FetchError as exc:
            return self._error(exc.message)
        done = self._done.get((normalize(url), mode))
        if done is not None:
            return self._page_result(done, mode, focus)
        host = host_of(url)
        if host not in self.allowed_hosts:
            if self.approve_host is not None and self.approve_host(host):
                self.allowed_hosts.add(host)
            else:
                return self._not_allowed(host)
        if self.fetched >= self.max_pages:
            return self._error(
                f"The page budget of {self.max_pages} fetches is used up. Stop fetching and answer with what you have.",
                limit="pages",
            )
        self.fetched += 1
        self._pace()
        try:
            page = self.fetch_fn(url, mode, timeout=self.timeout)
        except FetchError as exc:
            text = exc.message
            if exc.hint:
                text += f" {exc.hint}"
            if exc.retry_mode and exc.retry_mode != mode:
                text += f" Retry with mode='{exc.retry_mode}'."
            return self._error(text)
        finally:
            self._last_fetch = self._clock()
        if host_of(page.url) not in self.allowed_hosts:
            return self._error(
                f"{url} redirected to {page.url}, which is not an allowed site, so it was discarded. "
                f"Allowed sites: {', '.join(sorted(self.allowed_hosts))}."
            )
        self._store(page)
        self._done[(normalize(url), mode)] = page
        if self.on_page:
            self.on_page(page)
        return self._page_result(page, mode, focus)

    def _list_links(self, args: dict) -> ToolResult:
        page = self._find(self._str(args, "url", required=True))
        contains = self._str(args, "contains")
        limit = self._int(args, "limit", 50, 1, MAX_LINKS)
        links = [link for link in page.links if not contains or contains.lower() in link.lower()]
        shown = links[:limit]
        lines = [
            f"- {link}" + ("" if host_of(link) in self.allowed_hosts else "  [off-site: not fetchable]") for link in shown
        ]
        label = f" containing {contains!r}" if contains else ""
        head = f"{len(links)} of {len(page.links)} links{label} on {page.url}; showing {len(shown)}."
        return self._ok("\n".join([head, *lines]))

    def _read_page(self, args: dict) -> ToolResult:
        page = self._find(self._str(args, "url", required=True))
        focus = self._str(args, "focus")
        budget = self._int(args, "max_tokens", self.result_tokens, 200, self.result_tokens)
        return self._ok(f"{page.url}\n{view_page(page, focus, budget)}")

    def _extract(self, args: dict) -> ToolResult:
        page = self._find(self._str(args, "url", required=True))
        selector = self._str(args, "selector", required=True)
        attr = self._str(args, "attr")
        limit = self._int(args, "limit", 50, 1, 500)
        if not page.html:
            raise ToolArgError("This page has no stored HTML to run a selector on. Fetch it again with fetch_page.")
        try:
            elements = Selector(page.html, url=page.url).css(selector)
        except Exception as exc:
            raise ToolArgError(f"Invalid CSS selector {selector!r}: {exc}") from None
        values = []
        for element in elements:
            value = element.attrib.get(attr) if attr else " ".join(element.get_all_text().split())
            value = str(value).strip() if value is not None else ""
            if not value:
                continue
            if attr in ("href", "src", "action"):
                value = urljoin(page.url, value)
            values.append(value)
        if not values:
            suffix = f" with attribute {attr!r}" if attr else ""
            return self._ok(f"0 matches for {selector!r}{suffix} on {page.url}.")
        shown = values[:limit]
        lines = [f"{i}. {value}" for i, value in enumerate(shown, 1)]
        return self._ok(f"{len(values)} match(es) for {selector!r} on {page.url}; showing {len(shown)}:\n" + "\n".join(lines))

    def _set_fetch_delay(self, args: dict) -> ToolResult:
        wanted = self._seconds(args, "seconds")
        self.delay = min(self.max_delay, max(self.min_delay, wanted))
        if self.on_delay:
            self.on_delay(self.delay)
        text = f"Fetch delay is now {self.delay:g}s between requests."
        if wanted < self.min_delay:
            text += f" That is the minimum allowed; {wanted:g}s is too fast."
        elif wanted > self.max_delay:
            text += f" That is the maximum allowed; {wanted:g}s is too slow."
        return self._ok(text)

    def _add_rows(self, args: dict) -> ToolResult:
        rows = args.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ToolArgError("'rows' must be a non-empty list of objects.")
        if len(rows) > MAX_ROWS_PER_CALL:
            raise ToolArgError(f"Add at most {MAX_ROWS_PER_CALL} rows per call.")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ToolArgError(f'Row {index} is not an object. Every row must be an object like {{"name": "...", "price": "..."}}.')
        added: list[dict] = []
        for row in rows:
            row = dict(row)
            if row not in self.rows and row not in added:
                added.append(row)
        self.rows.extend(added)
        if added and self.on_rows:
            self.on_rows(added)
        columns = list(dict.fromkeys(key for row in self.rows for key in row))
        skipped = len(rows) - len(added)
        note = f" ({skipped} duplicate rows ignored)" if skipped else ""
        return self._ok(
            f"Added {len(added)} rows{note}. The table now has {len(self.rows)} rows with columns: {', '.join(columns)}.",
            rows_added=len(added),
        )


def build_tools(box: ToolBox) -> list:
    """LangChain tools around a ToolBox, plus ask_user. Pauses are LangGraph interrupts."""

    def run(name: str, args: dict) -> tuple[str, dict]:
        result = box.execute(name, args)
        artifact = {
            "ok": not result.is_error,
            "rows_added": result.rows_added,
            "fetched": list(result.fetched),
            "limit": result.limit,
        }
        return result.text, artifact

    def approve_host(host: str) -> bool:
        decision = interrupt({"kind": "site", "host": host, "question": f"May I fetch pages from {host}?"})
        return bool(decision.get("allow")) if isinstance(decision, dict) else str(decision).lower() in ("allow", "yes", "true")

    box.approve_host = approve_host

    @tool(response_format="content_and_artifact", parse_docstring=True)
    def fetch_page(url: str, mode: str = "fast", focus: str | None = None) -> tuple[str, dict]:
        """Fetch a web page, store it for the other tools, and return its text (trimmed).

        Use mode 'fast' for normal pages, 'dynamic' for pages rendered by JavaScript, and 'stealthy' for sites that block simple requests.

        Args:
            url: Full page URL.
            mode: Fetch mode: fast, dynamic or stealthy. Default fast.
            focus: Optional keywords. Long pages then return only the most relevant parts.
        """
        return run("fetch_page", {"url": url, "mode": mode, "focus": focus})

    @tool(response_format="content_and_artifact", parse_docstring=True)
    def list_links(url: str, contains: str | None = None, limit: int | None = None) -> tuple[str, dict]:
        """List the links found on a page you already fetched. Off-site links are marked and cannot be fetched.

        Args:
            url: URL of a fetched page.
            contains: Optional text the link must contain.
            limit: Maximum links to show. Default 50.
        """
        return run("list_links", {"url": url, "contains": contains, "limit": limit})

    @tool(response_format="content_and_artifact", parse_docstring=True)
    def read_page(url: str, focus: str | None = None, max_tokens: int | None = None) -> tuple[str, dict]:
        """Read a page you already fetched again, or only the parts relevant to a focus.

        Args:
            url: URL of a fetched page.
            focus: Optional keywords to find the relevant parts.
            max_tokens: Optional size limit for the answer.
        """
        return run("read_page", {"url": url, "focus": focus, "max_tokens": max_tokens})

    @tool(response_format="content_and_artifact", parse_docstring=True)
    def extract(url: str, selector: str, attr: str | None = None, limit: int | None = None) -> tuple[str, dict]:
        """Run a CSS selector on a page you already fetched and return the matching text, or an attribute value such as href.

        Args:
            url: URL of a fetched page.
            selector: CSS selector, for example '.product .price'.
            attr: Optional attribute to return instead of the text, for example 'href'.
            limit: Maximum matches to show. Default 50.
        """
        return run("extract", {"url": url, "selector": selector, "attr": attr, "limit": limit})

    @tool(response_format="content_and_artifact", parse_docstring=True)
    def add_rows(rows: list[dict]) -> tuple[str, dict]:
        """Add rows to the results table the user can download. One object per item, with consistent keys. Call it as you go.

        Args:
            rows: Objects such as [{"name": "Starter", "price": "$10"}].
        """
        return run("add_rows", {"rows": rows})

    @tool(response_format="content_and_artifact", parse_docstring=True)
    def set_fetch_delay(seconds: float, reason: str | None = None) -> tuple[str, dict]:
        """Set the pause between page fetches. Raise it when a site answers 429, times out or blocks you; it cannot go below the user's minimum.

        Args:
            seconds: Seconds to wait between requests, for example 2.
            reason: Short note on why, shown to the user.
        """
        return run("set_fetch_delay", {"seconds": seconds, "reason": reason})

    @tool(response_format="content_and_artifact", parse_docstring=True)
    def ask_user(question: str, options: list[str] | None = None) -> tuple[str, dict]:
        """Ask the human a question and wait for the answer. Use it when the instruction is ambiguous, a login or CAPTCHA blocks the page, every fetch mode failed, or a choice changes the outcome.

        Args:
            question: A short, specific question.
            options: Optional answers to offer as buttons.
        """
        answer = interrupt({"kind": "ask", "question": question, "options": list(options or [])})
        return f"The user answered: {answer}", {"ok": True, "rows_added": 0, "fetched": [], "limit": None}

    return [fetch_page, list_links, read_page, extract, add_rows, set_fetch_delay, ask_user]
