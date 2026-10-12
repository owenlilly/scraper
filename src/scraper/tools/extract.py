"""extract: run a CSS selector on a page that was already fetched."""

from urllib.parse import urljoin

from langchain_core.tools import tool
from scrapling.parser import Selector

from .result import ToolArgError, ToolResult, int_arg, str_arg

NAME = "extract"
SUMMARY = "Pull text or links out of a page with a CSS selector."  # in plain words, for the person and for the Ask-mode prompt


def run(box, args: dict) -> ToolResult:
    page = box.find(str_arg(args, "url", required=True))
    selector = str_arg(args, "selector", required=True)
    attr = str_arg(args, "attr")
    limit = int_arg(args, "limit", 50, 1, 500)
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
        return box.ok(f"0 matches for {selector!r}{suffix} on {page.url}.")
    shown = values[:limit]
    lines = [f"{i}. {value}" for i, value in enumerate(shown, 1)]
    return box.ok(f"{len(values)} match(es) for {selector!r} on {page.url}; showing {len(shown)}:\n" + "\n".join(lines))


def make_tool(call):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def extract(url: str, selector: str, attr: str | None = None, limit: int | None = None) -> tuple[str, dict]:
        """Run a CSS selector on a page you already fetched and return the matching text, or an attribute value such as href.

        Args:
            url: URL of a fetched page.
            selector: CSS selector, for example '.product .price'.
            attr: Optional attribute to return instead of the text, for example 'href'.
            limit: Maximum matches to show. Default 50.
        """
        return call(NAME, {"url": url, "selector": selector, "attr": attr, "limit": limit})

    return extract
