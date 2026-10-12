"""The ToolBox: what the tools share (the pages, the rows, the allowlist, the page budget, the pause between fetches) and the way a
call by name reaches the right tool."""

import time
from collections.abc import Callable

from langgraph.errors import GraphBubbleUp

from .. import debuglog
from ..browser import Browser
from ..crawl import normalize
from ..fetch import MODES, Page
from . import add_rows, browser_click, browser_network, browser_scroll, browser_submit_form, extract, fetch_page, list_links, read_page, set_fetch_delay, web_search
from .result import ToolArgError, ToolResult

# Tools that have logic to run, by the name the model calls them. (ask_user only pauses for the person.)
TOOLS_WITH_LOGIC = (fetch_page, list_links, read_page, extract, add_rows, set_fetch_delay)
# Added only when the box is given a browser.
BROWSER_TOOLS = (browser_submit_form, browser_click, browser_scroll, browser_network)


class ToolBox:
    def __init__(
        self,
        *,
        pages: list[Page],
        rows: list[dict],
        fetch_fn: Callable[..., Page],
        browser: Browser | None = None,
        search: Callable[..., list] | None = None,
        fallback: bool = True,
        debug_context: dict | None = None,
        allowed_hosts: set[str],
        max_pages: int = 30,
        result_tokens: int = 3000,
        timeout: float = 20.0,
        fetched: int = 0,
        grant: int | None = None,
        allowed_modes: tuple[str, ...] = MODES,
        min_delay: float = 0.0,
        max_delay: float = set_fetch_delay.MAX_FETCH_DELAY,
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
        self.browser = browser
        self.search = search  # (query, num_results=, objective=, timeout=) -> results; None when this session cannot search the web
        self.debug_context = dict(debug_context or {})  # who and which run, added to everything debug mode records during a tool call
        self.fallback = fallback  # try the other fetch modes when a page is blocked or comes back empty
        self.allowed_hosts = set(allowed_hosts)
        self.max_pages = max_pages
        self.result_tokens = result_tokens
        self.timeout = timeout
        self.fetched = fetched
        self.allowed_modes = allowed_modes
        self.approve_host = approve_host
        self.on_page = on_page
        self.on_rows = on_rows
        self.on_delay = on_delay
        self.min_delay = min_delay
        self.max_delay = max(min_delay, max_delay)
        self.delay = min(self.max_delay, max(min_delay, delay))  # the pause kept between two fetches
        self.clock = clock
        self.sleep = sleep
        self.last_fetch: float | None = None
        self.done: dict[tuple[str, str], Page] = {}  # pages fetched already this run, so asking again costs nothing
        self._grant = grant if grant is not None else max_pages
        self._handlers = {module.NAME: module.run for module in TOOLS_WITH_LOGIC}
        if search is not None:
            self._handlers[web_search.NAME] = web_search.run
        if browser is not None:
            self._handlers.update({module.NAME: module.run for module in BROWSER_TOOLS})

    def grant_pages(self) -> None:
        self.max_pages += self._grant

    # ---- entry point ---------------------------------------------------------

    def execute(self, name: str, args: dict) -> ToolResult:
        with debuglog.scope(**self.debug_context, tool=name):
            debuglog.event("tool.call", args=args if isinstance(args, dict) else repr(args))
            elapsed = debuglog.stopwatch()
            try:
                result = self._execute(name, args)
            except GraphBubbleUp:
                debuglog.event("tool.paused", ms=elapsed())
                raise
            debuglog.event(
                "tool.result", ok=not result.is_error, ms=elapsed(), chars=len(result.text),
                rows_added=result.rows_added, fetched=list(result.fetched), limit=result.limit, preview=debuglog.preview(result.text),
            )
            return result

    def _execute(self, name: str, args: dict) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return self.error(f"Unknown tool {name!r}. Available tools: {', '.join(self._handlers)}.")
        if not isinstance(args, dict):
            return self.error("Tool arguments must be a JSON object.")
        try:
            return handler(self, args)
        except GraphBubbleUp:
            raise  # a pause (interrupt) must reach LangGraph; it is not a tool error
        except ToolArgError as exc:
            return self.error(str(exc))
        except Exception as exc:  # a bug or surprise inside a tool must not end the run
            return self.error(f"{name} failed: {type(exc).__name__}: {exc}")

    # ---- shared by the tools -------------------------------------------------

    def cap(self, text: str) -> str:
        limit = self.result_tokens * 4
        return text if len(text) <= limit else text[:limit].rstrip() + "\n[output truncated]"

    def ok(self, text: str, **kwargs) -> ToolResult:
        return ToolResult(self.cap(text), **kwargs)

    def error(self, text: str, **kwargs) -> ToolResult:
        return ToolResult(self.cap(text), is_error=True, **kwargs)

    def pace(self) -> None:
        """Wait out what is left of the delay since the previous request ended."""
        if self.last_fetch is not None and self.delay:
            remaining = self.delay - (self.clock() - self.last_fetch)
            if remaining > 0:
                self.sleep(remaining)

    def find(self, url: str) -> Page:
        key = normalize(url)
        for page in self.pages:
            if normalize(page.url) == key:
                return page
        stored = ", ".join(page.url for page in self.pages[:10]) or "none"
        raise ToolArgError(f"No stored page for {url}. Stored pages: {stored}. Call fetch_page first.")

    def store(self, page: Page) -> None:
        key = normalize(page.url)
        for index, existing in enumerate(self.pages):
            if normalize(existing.url) == key:
                self.pages[index] = page
                return
        self.pages.append(page)
