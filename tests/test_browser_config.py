from scraper.browser import DEFAULT_BROWSER
from scraper.config import Config, load
from scraper.session import agent_system_prompt
from scraper.tools import ToolBox, build_tools

BROWSER_TOOLS = ["browser_submit_form", "browser_click", "browser_scroll", "browser_network"]


def test_the_browser_is_on_unless_switched_off():
    assert Config().browser is True
    assert load({"SCRAPER_BROWSER": "false"}).browser is False


def test_the_agent_is_told_about_the_browser_tools_only_when_it_has_them():
    on, off = agent_system_prompt(Config()), agent_system_prompt(Config(browser=False))
    for name in BROWSER_TOOLS:
        assert name in on and name not in off


def test_the_model_is_told_each_browser_tool_uses_a_real_browser_so_it_connects_them_with_a_request_to_use_one():
    box = ToolBox(pages=[], rows=[], fetch_fn=lambda *a, **k: None, browser=DEFAULT_BROWSER, allowed_hosts=set())
    tools = {t.name: t.description.lower() for t in build_tools(box)}
    for name in BROWSER_TOOLS:
        assert "browser" in tools[name], name
    assert all("playwright" in tools[name] for name in ("browser_submit_form", "browser_click"))
    assert "type" in tools["browser_submit_form"] and "click" in tools["browser_click"] and "scroll" in tools["browser_scroll"]
    assert "network" in tools["browser_network"] and "json" in tools["browser_network"]
    prompt = agent_system_prompt(Config()).lower()
    assert "browser" in prompt and "playwright" in prompt
