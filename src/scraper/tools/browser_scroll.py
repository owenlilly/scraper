"""browser_scroll: scroll a page to the bottom in a real browser, so content that loads on scroll appears, and return the page afterwards."""

from langchain_core.tools import tool

from ..browser import MAX_SCROLLS
from .pages import page_size, run_browser, show_page
from .result import ToolResult, int_arg, str_arg

NAME = "browser_scroll"
SUMMARY = "Use a real browser to scroll a page so content that loads on scroll appears."  # in plain words, for the person and for the Ask-mode prompt


def run(box, args: dict) -> ToolResult:
    url = str_arg(args, "url", required=True)
    times = int_arg(args, "times", 3, 1, MAX_SCROLLS)

    def present(opened: str, page) -> ToolResult:
        header = f"Scrolled {opened} to the bottom (up to {times} times). The page is now {page.url}: {page_size(page)}.\n"
        return show_page(box, page, header)

    return run_browser(box, url, lambda opened, allow: box.browser.scroll(opened, times, timeout=box.timeout, allow_request=allow), present)


def make_tool(call):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def browser_scroll(url: str, times: int | None = None) -> tuple[str, dict]:
        """Use a real browser to open a page and scroll to the bottom, so content that only loads as you scroll (infinite scroll, lazy images and lists) appears, then return the page as it is afterwards.

        Args:
            url: The page to open.
            times: How many times to scroll to the bottom. Default 3, at most 20. Stops early once the page stops growing.
        """
        return call(NAME, {"url": url, "times": times})

    return browser_scroll
