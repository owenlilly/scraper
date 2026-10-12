"""read_page: read a page that was already fetched again, or only the parts relevant to a focus."""

from langchain_core.tools import tool

from ..context import view_page
from .result import ToolResult, int_arg, str_arg

NAME = "read_page"
SUMMARY = "Read a stored page again, or only the parts about a topic."  # in plain words, for the person and for the Ask-mode prompt


def run(box, args: dict) -> ToolResult:
    page = box.find(str_arg(args, "url", required=True))
    focus = str_arg(args, "focus")
    budget = int_arg(args, "max_tokens", box.result_tokens, 200, box.result_tokens)
    return box.ok(f"{page.url}\n{view_page(page, focus, budget)}")


def make_tool(call):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def read_page(url: str, focus: str | None = None, max_tokens: int | None = None) -> tuple[str, dict]:
        """Read a page you already fetched again, or only the parts relevant to a focus.

        Args:
            url: URL of a fetched page.
            focus: Optional keywords to find the relevant parts.
            max_tokens: Optional size limit for the answer.
        """
        return call(NAME, {"url": url, "focus": focus, "max_tokens": max_tokens})

    return read_page
