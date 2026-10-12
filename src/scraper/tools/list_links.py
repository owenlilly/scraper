"""list_links: list the links on a page that was already fetched."""

from langchain_core.tools import tool

from .hosts import host_of
from .result import ToolResult, int_arg, str_arg

NAME = "list_links"
SUMMARY = "List the links on a page it has read."  # in plain words, for the person and for the Ask-mode prompt
MAX_LINKS = 200


def run(box, args: dict) -> ToolResult:
    page = box.find(str_arg(args, "url", required=True))
    contains = str_arg(args, "contains")
    limit = int_arg(args, "limit", 50, 1, MAX_LINKS)
    links = [link for link in page.links if not contains or contains.lower() in link.lower()]
    shown = links[:limit]
    lines = [f"- {link}" + ("" if host_of(link) in box.allowed_hosts else "  [off-site: not fetchable]") for link in shown]
    label = f" containing {contains!r}" if contains else ""
    head = f"{len(links)} of {len(page.links)} links{label} on {page.url}; showing {len(shown)}."
    return box.ok("\n".join([head, *lines]))


def make_tool(call):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def list_links(url: str, contains: str | None = None, limit: int | None = None) -> tuple[str, dict]:
        """List the links found on a page you already fetched. Off-site links are marked and cannot be fetched.

        Args:
            url: URL of a fetched page.
            contains: Optional text the link must contain.
            limit: Maximum links to show. Default 50.
        """
        return call(NAME, {"url": url, "contains": contains, "limit": limit})

    return list_links
