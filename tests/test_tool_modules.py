"""Each agent tool lives in its own module under scraper/tools, named after the tool, so it is easy to find."""

import importlib
import pkgutil

import scraper.tools as tools_package
from scraper.browser import DEFAULT_BROWSER
from scraper.tools import ToolBox, build_tools
from fakes import make_page

BROWSER_TOOLS = ["browser_submit_form", "browser_click", "browser_scroll", "browser_network"]
TOOLS = ["fetch_page", "web_search", *BROWSER_TOOLS, "list_links", "read_page", "extract", "add_rows", "set_fetch_delay", "ask_user"]


def _box(forms: bool = True, search: bool = True) -> ToolBox:
    return ToolBox(
        pages=[], rows=[], fetch_fn=lambda url, mode, timeout=20.0: make_page(url), allowed_hosts={"site.test"},
        browser=DEFAULT_BROWSER if forms else None,
        search=(lambda *a, **k: []) if search else None,
    )


def test_every_tool_has_a_module_named_after_it():
    for name in TOOLS:
        module = importlib.import_module(f"scraper.tools.{name}")
        assert module.NAME == name
        assert callable(module.make_tool)


def test_the_agent_is_given_exactly_these_tools_in_this_order():
    assert [t.name for t in build_tools(_box())] == TOOLS


def test_the_browser_tools_are_offered_only_when_the_session_has_a_browser():
    assert [t.name for t in build_tools(_box(forms=False))] == [name for name in TOOLS if name not in BROWSER_TOOLS]
    assert [t.name for t in build_tools(_box(search=False))] == [name for name in TOOLS if name != "web_search"]
    assert not set(BROWSER_TOOLS) & set(_box(forms=False)._handlers)


def test_the_toolbox_runs_every_tool_that_has_logic_by_the_name_of_its_module():
    handlers = set(_box()._handlers)
    assert handlers == {name for name in TOOLS if hasattr(importlib.import_module(f"scraper.tools.{name}"), "run")}
    assert handlers == set(TOOLS) - {"ask_user"}  # ask_user only pauses for the person; it has no logic to run


def test_the_package_holds_nothing_but_the_tools_and_their_shared_parts():
    modules = {m.name for m in pkgutil.iter_modules(tools_package.__path__)}
    assert modules == set(TOOLS) | {"hosts", "result", "toolbox", "build", "pages"}
