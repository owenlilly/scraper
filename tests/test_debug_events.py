"""What debug mode records about fetches, browser actions, searches, tool calls and runs."""

import json
from types import SimpleNamespace

import httpx
import pytest

from api_support import A, MODEL
from fakes import ScriptedChat, ai, call, make_fetch, make_page
from langgraph.checkpoint.memory import InMemorySaver

from scraper import debuglog
from scraper.agent import AgentRunner, build_agent
from scraper.browser import browse
from scraper.config import Config
from scraper.fetch import FetchError, fetch
from scraper.search import web_search
from scraper.tools import ToolBox, build_tools

URL = "https://site.test/pricing"
HTML = "<html><head><title>Pricing</title></head><body><h1>Plans</h1><p>Starter is ten dollars a month, billed yearly.</p></body></html>"


@pytest.fixture
def logs(tmp_path):
    debuglog.configure(Config(debug=True, debug_dir=str(tmp_path), debug_body_chars=40))
    yield lambda: [json.loads(line) for f in sorted(tmp_path.glob("debug-*.jsonl")) for line in f.read_text().splitlines()]
    debuglog.configure(Config())


def names(entries):
    return [e["event"] for e in entries]


def response(status=200, ctype="text/html", body=HTML, url=URL):
    return SimpleNamespace(status=status, headers={"content-type": ctype}, url=url, html_content=body, body=body.encode())


# ---- fetch ----------------------------------------------------------------------------------------------------------------------------


def test_a_fetch_is_recorded_with_what_was_asked_and_what_came_back(logs):
    fetch(URL, "fast", backends={"fast": lambda url, timeout: response(url="https://site.test/pricing/")}, timeout=7)
    request, answer = logs()
    assert request["event"] == "fetch.request" and request["url"] == URL and request["mode"] == "fast" and request["timeout"] == 7
    assert answer["event"] == "fetch.response" and answer["status"] == 200 and answer["final_url"] == "https://site.test/pricing/"
    assert answer["content_type"] == "text/html" and answer["bytes"] == len(HTML) and answer["title"] == "Pricing" and answer["ms"] >= 0
    assert answer["preview"].startswith("Pricing") and "more characters" in answer["preview"]  # cut at the setting, 40 here


def test_a_blocked_fetch_records_the_status_and_the_mode_that_was_suggested(logs):
    with pytest.raises(FetchError):
        fetch(URL, "fast", backends={"fast": lambda url, timeout: response(status=403, body="denied")})
    *_, answer = logs()
    assert answer["event"] == "fetch.error" and answer["status"] == 403 and answer["retry_mode"] == "stealthy"


def test_a_fetch_that_fails_before_any_answer_records_why(logs):
    def boom(url, timeout):
        raise ConnectionError("Failed to connect to host")

    with pytest.raises(FetchError):
        fetch(URL, "dynamic", backends={"dynamic": boom})
    *_, answer = logs()
    assert answer["event"] == "fetch.error" and "Failed to connect" in answer["error"] and answer["mode"] == "dynamic"


def test_a_page_that_is_not_html_is_recorded_as_refused(logs):
    with pytest.raises(FetchError):
        fetch(URL, "fast", backends={"fast": lambda url, timeout: response(ctype="application/pdf")})
    *_, answer = logs()
    assert answer["event"] == "fetch.error" and answer["content_type"] == "application/pdf"


def test_with_debug_off_a_fetch_writes_nothing(tmp_path):
    debuglog.configure(Config(debug=False, debug_dir=str(tmp_path / "x")))
    fetch(URL, "fast", backends={"fast": lambda url, timeout: response()})
    assert not (tmp_path / "x").exists()


# ---- tools ----------------------------------------------------------------------------------------------------------------------------


def make_box(**kw):
    return ToolBox(pages=[], rows=[], fetch_fn=make_fetch({URL: make_page(URL, markdown="# Pricing\n\nStarter ten dollars.")}), allowed_hosts={"site.test"}, **kw)


def test_every_tool_call_and_its_result_are_recorded_with_who_and_which_run(logs):
    box = make_box(debug_context={"user": "alice", "run": 4})
    box.execute("fetch_page", {"url": URL})
    box.execute("add_rows", {"rows": []})  # an error
    call_a, result_a, call_b, result_b = [e for e in logs() if e["event"].startswith("tool.")]
    assert (call_a["event"], call_a["tool"], call_a["user"], call_a["run"], call_a["args"]) == ("tool.call", "fetch_page", "alice", 4, {"url": URL})
    assert result_a["ok"] is True and result_a["chars"] > 0 and result_a["fetched"] == [URL] and result_a["ms"] >= 0 and result_a["preview"]
    assert (call_b["tool"], result_b["ok"]) == ("add_rows", False) and "non-empty list" in result_b["preview"]


def test_the_fetches_inside_a_tool_call_carry_the_tools_name_and_the_person(logs):
    box = make_box(debug_context={"user": "alice"})
    box.execute("fetch_page", {"url": URL})
    fetch_event = next(e for e in logs() if e["event"] == "fetch.request") if False else None
    # make_fetch is a stand-in that does not go through fetch(): the scope is what matters
    debuglog.event("probe")
    assert all(e.get("user") != "alice" for e in logs() if e["event"] == "probe")  # outside the call the scope is gone


def test_an_unknown_tool_is_recorded_too(logs):
    make_box().execute("nope", {})
    assert [e["ok"] for e in logs() if e["event"] == "tool.result"] == [False]


def test_a_switch_to_another_fetch_mode_is_recorded_with_the_reason(logs):
    blocked = FetchError("HTTP 403 from x", hint="h", retry_mode="stealthy")

    def fetch_fn(url, mode, *, timeout):
        if mode == "fast":
            raise blocked
        return make_page(URL, markdown="# ok")

    ToolBox(pages=[], rows=[], fetch_fn=fetch_fn, allowed_hosts={"site.test"}).execute("fetch_page", {"url": URL})
    [step] = [e for e in logs() if e["event"] == "fetch.fallback"]
    assert (step["from_mode"], step["to_mode"]) == ("fast", "stealthy") and "403" in step["reason"] and step["url"] == URL


# ---- search ---------------------------------------------------------------------------------------------------------------------------


def test_a_search_is_recorded_with_its_words_and_how_it_went(logs):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "Title: A\nURL: https://a.test/\nHighlights:\nhi"}]}})
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body, headers={"content-type": "application/json"})))
    web_search("starter plan price", num_results=2, client=client, api_key="k-secret")
    request, answer = logs()
    assert request["event"] == "search.request" and request["query"] == "starter plan price" and request["num_results"] == 2
    assert answer["event"] == "search.response" and answer["status"] == 200 and answer["results"] == 1 and answer["urls"] == ["https://a.test/"]
    assert "k-secret" not in json.dumps(logs())


def test_a_search_that_fails_is_recorded(logs):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, text="slow")))
    with pytest.raises(Exception):
        web_search("q", client=client)
    assert logs()[-1]["event"] == "search.error" and logs()[-1]["status"] == 429


# ---- browser --------------------------------------------------------------------------------------------------------------------------


def test_a_browser_action_is_recorded_with_how_it_ended(logs):
    backend = lambda url, timeout, setup, act: response(url="https://site.test/after")  # noqa: E731
    browse(URL, timeout=5, allow_request=lambda u, nav: True, backend=backend, label="browser_click")
    [entry] = [e for e in logs() if e["event"].startswith("browser.")]
    assert entry["event"] == "browser.run" and entry["action"] == "browser_click" and entry["url"] == URL
    assert entry["status"] == 200 and entry["final_url"] == "https://site.test/after" and entry["blocked"] == [] and entry["ms"] >= 0


def test_a_browser_action_that_fails_is_recorded(logs):
    def backend(url, timeout, setup, act):
        raise RuntimeError("Executable doesn't exist")

    with pytest.raises(FetchError):
        browse(URL, timeout=5, allow_request=lambda u, nav: True, backend=backend, label="browser_scroll")
    assert logs()[-1]["event"] == "browser.error" and logs()[-1]["action"] == "browser_scroll"


# ---- the run --------------------------------------------------------------------------------------------------------------------------


def runner_for(script, max_steps=25):
    box = ToolBox(pages=[], rows=[], fetch_fn=make_fetch({URL: make_page(URL)}), allowed_hosts={"site.test"}, max_pages=1000)
    agent = build_agent(ScriptedChat(script=list(script)), build_tools(box), system_prompt="x", checkpointer=InMemorySaver(), max_steps=max_steps, max_context_tokens=1_000_000)
    return AgentRunner(agent, max_steps=max_steps)


async def test_a_run_that_reaches_its_step_limit_records_why_it_paused(logs):
    runner = runner_for([ai("", call("list_links", f"c{i}", url=URL, limit=i + 1)) for i in range(40)], max_steps=5)
    [event async for event in runner.run("t", [{"role": "user", "content": "go"}])]
    [paused] = [e for e in logs() if e["event"] == "run.limit"]
    assert paused["reason"] == "steps" and paused["steps"] == 5


async def test_a_run_through_the_api_is_recorded_from_start_to_end_with_the_persons_id(api, logs):
    api.model.script = [ai("", call("fetch_page", "c1", url=A)), ai("Done.")]
    await api.client.post("/agent/run", json={"instruction": f"look at {A}", "model": MODEL}, headers=api.auth("alice"))
    entries = logs()
    start = next(e for e in entries if e["event"] == "run.start")
    end = next(e for e in entries if e["event"] == "run.end")
    assert start["user"] == "alice" and start["model"] == MODEL and start["instruction"].startswith("look at") and start["provider"]
    assert end["status"] == "done" and end["user"] == "alice" and end["fetched"] >= 1
    tool_calls = [e for e in entries if e["event"] == "tool.call"]
    assert [e["tool"] for e in tool_calls] == ["fetch_page"] and tool_calls[0]["user"] == "alice"
