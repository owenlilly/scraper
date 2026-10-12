import pytest

from api_support import MODEL, Harness
from fakes import ai
from scraper.config import Config, load
from scraper.hosted import make_hosted_search
from scraper.search import EXA_URL, SearchResult
from scraper.session import agent_system_prompt
from scraper.usage import MemoryUsageStore, UsageLimitReached, UsageMeter


def test_web_search_is_on_unless_switched_off_and_uses_exas_open_endpoint():
    cfg = Config()
    assert cfg.web_search is True and cfg.exa_url == EXA_URL and cfg.exa_api_key == ""
    assert load({"SCRAPER_WEB_SEARCH": "false", "SCRAPER_EXA_API_KEY": "k"}).web_search is False
    assert "secret-key" not in repr(load({"SCRAPER_EXA_API_KEY": "secret-key"}))  # a key is never printed


def test_the_agent_is_told_about_search_only_when_it_has_it():
    assert "web_search" in agent_system_prompt(Config())
    assert "web_search" not in agent_system_prompt(Config(web_search=False))


async def run_once(h):
    h.model.script = [ai("Done.")]
    response = await h.client.post("/agent/run", json={"instruction": "look something up", "model": MODEL}, headers=h.auth())
    assert response.status_code == 200
    return h.model.calls[-1]["tools"]


async def test_a_session_offers_the_search_tool_by_default(api):
    assert "web_search" in await run_once(api)


async def test_the_operator_can_switch_search_off(pg):
    h = Harness(pg, web_search=False).build()
    try:
        assert "web_search" not in await run_once(h)
    finally:
        await h.close()


def hosted(**cfg):
    config = Config(hosted=True, **cfg)
    meter = UsageMeter("alice", MemoryUsageStore(), config)
    calls: list[dict] = []

    def base(query, **kwargs):
        calls.append({"query": query, **kwargs})
        return [SearchResult("T", "https://a.test/", "", "x")]

    return make_hosted_search(config, meter, base=base), meter, calls


def test_a_hosted_search_uses_the_configured_service_and_costs_a_page_of_the_allowance():
    search, meter, calls = hosted(exa_url="https://exa.example/mcp", exa_api_key="k-1")
    search("find it", num_results=3, objective="o", timeout=9.0)
    assert calls == [{"query": "find it", "num_results": 3, "objective": "o", "timeout": 9.0, "url": "https://exa.example/mcp", "api_key": "k-1"}]
    assert meter.snapshot()["pages"] == 1


def test_a_hosted_search_stops_when_the_allowance_is_spent():
    search, _, calls = hosted(daily_pages=1)
    search("a", num_results=5, objective=None, timeout=5.0)
    with pytest.raises(UsageLimitReached):
        search("b", num_results=5, objective=None, timeout=5.0)
    assert len(calls) == 1
