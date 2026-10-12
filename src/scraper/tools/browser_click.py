"""browser_click: click a button, link or tab on a page, in a real browser, and return the page afterwards ("Load more", tabs, accordions)."""

from langchain_core.tools import tool

from ..browser import MAX_CLICKS
from .pages import page_size, run_browser, show_page
from .result import ToolResult, int_arg, str_arg

NAME = "browser_click"
SUMMARY = "Use a real browser to click a button, link or tab, such as Load more."  # in plain words, for the person and for the Ask-mode prompt


def run(box, args: dict) -> ToolResult:
    url = str_arg(args, "url", required=True)
    target = str_arg(args, "click", required=True)
    times = int_arg(args, "times", 1, 1, MAX_CLICKS)

    def present(opened: str, page) -> ToolResult:
        repeat = f" (up to {times} times)" if times > 1 else ""
        header = f"Clicked {target!r} on {opened}{repeat}. The page is now {page.url}: {page_size(page)}.\n"
        return show_page(box, page, header)

    return run_browser(box, url, lambda opened, allow: box.browser.click(opened, target, times, timeout=box.timeout, allow_request=allow), present)


def make_tool(call):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def browser_click(url: str, click: str, times: int | None = None) -> tuple[str, dict]:
        """Use a real browser (Playwright) to open a page and click something on it, then return the page as it is afterwards. Use it for a "Load more" button, a tab, an accordion or a link that works by script. Click repeatedly to load everything.

        Args:
            url: The page to open.
            click: What to click: the text on the button, link or tab, such as 'Load more', or a CSS selector such as '#next'.
            times: How many times to click, for a button that loads more each time. Default 1, at most 10. Stops early when the button is gone.
        """
        return call(NAME, {"url": url, "click": click, "times": times})

    return browser_click
