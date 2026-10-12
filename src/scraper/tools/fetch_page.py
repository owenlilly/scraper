"""fetch_page: fetch a web page, store it for the other tools, and return its text."""

from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from langchain_core.tools import tool

from .. import debuglog
from ..crawl import normalize
from ..fetch import MODES, FetchError, Page, validate_url
from .hosts import host_of
from .pages import allow_host, page_result, page_size
from .result import ToolArgError, ToolResult, str_arg

NAME = "fetch_page"
SUMMARY = "Open a web page and read it (plain HTTP, or a browser for pages built by JavaScript)."  # in plain words, for the person and for the Ask-mode prompt
MODE_ALIASES = {
    "stealth": "stealthy",
    "browser": "dynamic",
    "js": "dynamic",
    "javascript": "dynamic",
    "http": "fast",
    "static": "fast",
}


def read_mode(box, value) -> str:
    if value is None or value == "":
        return "fast"
    if not isinstance(value, str):
        raise ToolArgError("Argument 'mode' must be a string.")
    mode = MODE_ALIASES.get(value.strip().lower(), value.strip().lower())
    if mode not in MODES:
        raise ToolArgError(f"Unknown mode {value!r}. Use one of: {', '.join(box.allowed_modes)}.")
    if mode not in box.allowed_modes:
        raise ToolArgError(f"The {mode} mode is not available on this server. Use one of: {', '.join(box.allowed_modes)}.")
    return mode


def split_mode_from_url(url: str, asked) -> tuple[str, object, str | None]:
    """A `mode=fast|dynamic|stealthy` query parameter is the model's mistake (the mode is its own argument), not part of the site's address:
    take it out, and use it as the mode unless a mode other than the default was really asked for. Returns (address, mode, note)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url, asked, None
    query = parse_qsl(parts.query, keep_blank_values=True)
    is_mode = lambda key, value: key.lower() == "mode" and value.lower() in MODES  # noqa: E731
    found = [value for key, value in query if is_mode(key, value)]
    if not found:
        return url, asked, None
    cleaned = urlunsplit(parts._replace(query=urlencode([(k, v) for k, v in query if not is_mode(k, v)])))
    chosen = found[0].lower() if asked in (None, "", "fast") else asked
    return cleaned, chosen, f"Removed 'mode={found[0]}' from the address: the mode is a separate argument, not part of the URL."


def run(box, args: dict) -> ToolResult:
    url = str_arg(args, "url", required=True)
    url, asked_mode, address_note = split_mode_from_url(url, args.get("mode"))
    mode = read_mode(box, asked_mode)
    focus = str_arg(args, "focus")
    try:
        url = validate_url(url)
    except FetchError as exc:
        return box.error(exc.message)
    fetch = _Fetch(box, url, mode, focus, address_note)
    cached = box.done.get((normalize(url), mode))
    if cached is not None:
        return fetch.show(cached, mode)  # asking again for a page already fetched costs nothing
    refused = allow_host(box, host_of(url))
    return refused if refused is not None else fetch.until_done()


class _Fetch:
    """One call of the tool: fetch the page, switching to a more capable mode when the site blocks the simple one or the page comes back
    empty, and say what was tried."""

    def __init__(self, box, url: str, mode: str, focus: str | None, address_note: str | None) -> None:
        self.box, self.url, self.focus, self.address_note = box, url, focus, address_note
        self.asked = self.mode = mode
        self.tried: list[tuple[str, str]] = []  # modes that did not do, and why
        self.empty: tuple[Page, str] | None = None  # an empty page from a simpler mode, kept in case the better mode fails

    # ---- the loop ----------------------------------------------------------------------------------------------------------------

    def until_done(self) -> ToolResult:
        while (result := self._attempt()) is None:
            pass
        return result

    def _attempt(self) -> ToolResult | None:
        """One fetch. A result ends the call; None means another mode has been chosen, so go round again."""
        box = self.box
        if box.fetched >= box.max_pages:
            return self._out_of_budget()
        box.fetched += 1
        box.pace()
        try:
            page = box.fetch_fn(self.url, self.mode, timeout=box.timeout)
        except FetchError as exc:
            return self._failed(exc)
        finally:
            box.last_fetch = box.clock()
        better = _modes_above(box, self.mode)
        if box.fallback and page.looks_empty and self.mode == "fast" and better:
            self.empty = (page, self.mode)
            self._switch(better[0], "the page looked nearly empty")
            return None
        return self.finish(page, self.mode)

    def _switch(self, to_mode: str, reason: str) -> None:
        self.tried.append((self.mode, reason))
        debuglog.event("fetch.fallback", url=self.url, from_mode=self.mode, to_mode=to_mode, reason=reason)
        self.mode = to_mode

    def _failed(self, error: FetchError) -> ToolResult | None:
        box = self.box
        following = _next_after_failure(box, error, self.mode)
        if following is not None:
            self._switch(following, error.message)
            return None
        if self.empty is not None:
            return self.finish(*self.empty)  # an empty page is more use than an error
        text = error.text
        if self.tried:
            text += " Tried " + ", then ".join([m for m, _ in self.tried] + [self.mode]) + "."
        elif error.retry_mode and error.retry_mode != self.mode and not box.fallback:
            text += f" Retry with mode='{error.retry_mode}'."
        return box.error(text)

    def _out_of_budget(self) -> ToolResult:
        if self.empty is not None:
            return self.finish(*self.empty)
        why = f" {self.tried[-1][1]}." if self.tried else ""
        return self.box.error(
            f"The page budget of {self.box.max_pages} fetches is used up.{why} Stop fetching and answer with what you have.", limit="pages"
        )

    # ---- the answer --------------------------------------------------------------------------------------------------------------

    def finish(self, page: Page, used: str) -> ToolResult:
        box = self.box
        if host_of(page.url) not in box.allowed_hosts:
            return box.error(
                f"{self.url} redirected to {page.url}, which is not an allowed site, so it was discarded. "
                f"Allowed sites: {', '.join(sorted(box.allowed_hosts))}."
            )
        box.store(page)
        key = normalize(self.url)
        box.done[(key, used)] = box.done[(key, self.asked)] = page
        if box.on_page:
            box.on_page(page)
        return self.show(page, used)

    def show(self, page: Page, used: str) -> ToolResult:
        """The page as the model sees it, with a note of anything that happened on the way."""
        box = self.box
        if self.tried:
            note = " Tried " + ", then ".join(f"{m} mode ({why})" for m, why in self.tried) + f"; this is {used} mode."
        elif page.looks_empty and used == "fast" and "dynamic" in box.allowed_modes:
            note = " The page looks nearly empty; it may need mode='dynamic'."
        else:
            note = ""
        header = f"Fetched {page.url} (mode {used}): {page_size(page)}.{note}\n"
        result = page_result(box, page, header, self.focus)
        return replace(result, text=f"{self.address_note}\n{result.text}") if self.address_note and not result.is_error else result


def _modes_above(box, mode: str) -> list[str]:
    return [m for m in MODES[MODES.index(mode) + 1 :] if m in box.allowed_modes]  # MODES runs from the simplest mode to the most capable


def _next_after_failure(box, error: FetchError, mode: str) -> str | None:
    """The mode to try after a fetch failed, or None. Only a failure that a more capable mode could fix counts: the site blocking plain
    requests. A missing page, a dead address or a timeout would fail the same way again."""
    if not box.fallback or error.retry_mode is None:
        return None
    return error.retry_mode if error.retry_mode in _modes_above(box, mode) else None


def make_tool(call):
    """The LangChain tool the model sees. `call(name, args)` runs the tool through the ToolBox."""

    @tool(response_format="content_and_artifact", parse_docstring=True)
    def fetch_page(url: str, mode: str = "fast", focus: str | None = None) -> tuple[str, dict]:
        """Fetch a web page, store it for the other tools, and return its text (trimmed).

        Use mode 'fast' for normal pages, 'dynamic' for pages rendered by JavaScript, and 'stealthy' for sites that block simple requests. The mode is its own argument, never in the address: do not add ?mode=fast to the URL.

        Args:
            url: Full page URL.
            mode: Fetch mode: fast, dynamic or stealthy. Default fast.
            focus: Optional keywords. Long pages then return only the most relevant parts.
        """
        return call(NAME, {"url": url, "mode": mode, "focus": focus})

    return fetch_page
