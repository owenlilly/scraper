"""browser_network: list the background requests a page makes in a real browser, such as the JSON that fills a list."""

from langchain_core.tools import tool

from ..browser import MAX_NETWORK_REQUESTS
from .pages import run_browser
from .result import ToolResult, int_arg, str_arg

NAME = "browser_network"
SUMMARY = "List the background requests a page makes, such as the JSON that fills a list."  # in plain words, for the person and for the Ask-mode prompt
PREVIEW_SHOWN = 300


def _size(count: int) -> str:
    return f"{count} B" if count < 1024 else f"{count / 1024:.1f} KB"


def run(box, args: dict) -> ToolResult:
    url = str_arg(args, "url", required=True)
    contains = str_arg(args, "contains")
    limit = int_arg(args, "limit", 30, 1, MAX_NETWORK_REQUESTS)

    def present(opened: str, requests) -> ToolResult:
        if not requests:
            what = f" with {contains!r} in the address" if contains else ""
            return box.ok(f"{opened} made no background requests (XHR or fetch calls){what} while it loaded.")
        lines = [f"- {r.method} {r.status} {r.content_type or 'unknown type'} {_size(r.size)} {r.url}" for r in requests]
        for index, r in enumerate(requests):
            if r.preview:
                lines[index] += "\n    " + " ".join(r.preview.split())[:PREVIEW_SHOWN]
        head = f"Background requests made by {opened} ({len(requests)} shown):"
        tail = "Read one of these addresses with fetch_page to see a JSON response in full."
        return box.ok("\n".join([head, *lines, tail]))

    return run_browser(
        box, url, lambda opened, allow: box.browser.network(opened, timeout=box.timeout, allow_request=allow, contains=contains, limit=limit), present
    )


def make_tool(call):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def browser_network(url: str, contains: str | None = None, limit: int | None = None) -> tuple[str, dict]:
        """Use a real browser to open a page and list the background network requests it makes (XHR and fetch calls) while it loads, with each one's method, status, type, size and the start of a JSON response. Use it to find the API behind a page: the JSON that fills a list is usually easier to read than the page. Then read that address with fetch_page.

        Args:
            url: The page to open.
            contains: Optional text the request's address must contain, such as 'api'.
            limit: Maximum requests to list. Default 30.
        """
        return call(NAME, {"url": url, "contains": contains, "limit": limit})

    return browser_network
