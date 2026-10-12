"""Web search through Exa's hosted MCP server (https://mcp.exa.ai/mcp), called over plain HTTP the way opencode does it: one JSON-RPC
`tools/call` request, answered as a short event stream. No MCP session is needed, and no key (a key only raises the rate limit)."""

import json
from dataclasses import dataclass

import httpx

from . import debuglog

EXA_URL = "https://mcp.exa.ai/mcp"
TOOL = "web_search_exa"
MAX_RESULTS = 10


class SearchError(Exception):
    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    @property
    def text(self) -> str:
        """The message with its hint, as the model is told."""
        return self.message + (f" {self.hint}" if self.hint else "")


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    published: str
    highlights: str


def _parse_block(block: str) -> SearchResult:
    fields = {"title": "", "url": "", "published": ""}
    lines = block.split("\n")
    highlights: list[str] = []
    in_highlights = False
    for line in lines:
        if in_highlights:
            highlights.append(line)
            continue
        for prefix, key in (("Title:", "title"), ("URL:", "url"), ("Published:", "published")):
            if line.startswith(prefix):
                value = line[len(prefix) :].strip()
                fields[key] = "" if value == "N/A" else value
                break
        else:
            if line.startswith("Highlights:"):
                in_highlights = True
    text = "\n".join(highlights).strip()
    if not fields["url"] and not fields["title"]:
        text = block.strip()  # not in the shape we know: keep it whole rather than lose it
    return SearchResult(fields["title"], fields["url"], fields["published"], text)


def _reply(body: str) -> dict:
    """The JSON-RPC reply, whether the server streamed it (`data:` lines) or sent plain JSON."""
    data = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
    try:
        return json.loads("\n".join(data) if data else body)
    except ValueError:
        raise SearchError("The search service's answer could not be read.") from None


def web_search(
    query: str,
    *,
    num_results: int = 5,
    objective: str | None = None,
    timeout: float = 20.0,
    url: str = EXA_URL,
    api_key: str = "",
    client: httpx.Client | None = None,
) -> list[SearchResult]:
    """Search the web. `objective` says what the search is for (it defaults to the query, because the server asks for one)."""
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if api_key:
        headers["x-api-key"] = api_key
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": TOOL, "arguments": {"query": query, "objective": objective or query, "numResults": max(1, min(num_results, MAX_RESULTS))}},
    }
    own = client is None
    http = client or httpx.Client(timeout=timeout)
    wanted = payload["params"]["arguments"]["numResults"]
    debuglog.event("search.request", query=query, objective=objective, num_results=wanted, url=url)
    ms = debuglog.stopwatch()
    try:
        results = _search(http, url, payload, headers, ms)
    finally:
        if own:
            http.close()
    return results


def _search(http: httpx.Client, url: str, payload: dict, headers: dict, ms) -> list[SearchResult]:
    try:
        response = http.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        debuglog.event("search.error", ms=ms(), error=f"{type(exc).__name__}: {exc}")
        raise SearchError("Could not reach the search service.", hint="Try again shortly.") from None
    status = response.status_code
    if status == 429:
        debuglog.event("search.error", ms=ms(), status=status, error="rate limited")
        raise SearchError("The search service is busy (rate limited).", hint="Try again shortly. Setting SCRAPER_EXA_API_KEY raises the limit.")
    if status >= 400:
        debuglog.event("search.error", ms=ms(), status=status, error=f"HTTP {status}")
        raise SearchError(f"The search service answered {status}.", hint="Try again shortly.")
    try:
        reply = _reply(response.text)
    except SearchError as exc:
        debuglog.event("search.error", ms=ms(), status=status, error=exc.message)
        raise
    if "error" in reply:
        message = str(reply["error"].get("message") or "The search failed.")
        debuglog.event("search.error", ms=ms(), status=status, error=message)
        raise SearchError(message)
    result = reply.get("result") or {}
    text = "\n\n".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text").strip()
    if result.get("isError"):
        debuglog.event("search.error", ms=ms(), status=status, error=text or "The search failed.")
        raise SearchError(text or "The search failed.")
    found = [_parse_block(block) for block in text.split("\n\n---\n\n") if block.strip()] if text else []
    debuglog.event("search.response", ms=ms(), status=status, results=len(found), urls=[r.url for r in found], preview=debuglog.preview(text))
    return found
