"""The LangChain tools the model is given, built around one ToolBox."""

from langgraph.types import interrupt

from . import add_rows, ask_user, extract, fetch_page, list_links, read_page, set_fetch_delay, web_search
from .toolbox import BROWSER_TOOLS, ToolBox

# In the order the model is shown them.
TOOL_MODULES = (fetch_page, web_search, *BROWSER_TOOLS, list_links, read_page, extract, add_rows, set_fetch_delay, ask_user)


def tool_summaries(browser: bool, search: bool = True) -> list[tuple[str, str]]:
    """Each tool the agent has, with a one-line description in plain words. The browser tools are left out when the browser is off, and web
    search when it is."""
    return [(m.NAME, m.SUMMARY) for m in TOOL_MODULES if (browser or m not in BROWSER_TOOLS) and (search or m is not web_search)]


def build_tools(box: ToolBox) -> list:
    """LangChain tools around a ToolBox, plus ask_user. Pauses are LangGraph interrupts."""

    def call(name: str, args: dict) -> tuple[str, dict]:
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
    return [module.make_tool(call) for module in TOOL_MODULES if (module not in BROWSER_TOOLS or box.browser is not None) and (module is not web_search or box.search is not None)]
