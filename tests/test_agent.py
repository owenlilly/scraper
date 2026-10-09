from contextlib import AsyncExitStack

import httpx
import openai
import pytest
from fakes import ScriptedChat, ai, call, make_fetch, make_page
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from scraper.agent import AgentRunner, build_agent, open_checkpointer
from scraper.tools import ToolBox, build_tools, host_of

URL = "https://site.test/pricing"
OTHER = "https://other.test/x"
MESSAGES = [{"role": "user", "content": "go"}]
WEB = {URL: make_page(URL, markdown="# Pricing\n\nStarter ten dollars."), OTHER: make_page(OTHER)}


def setup(script, *, web=None, hosts=("site.test",), saver=None, max_steps=25, max_tokens=100_000, max_pages=30, provider="LM Studio", followups=None):
    pages, rows = [], []
    box = ToolBox(pages=pages, rows=rows, fetch_fn=make_fetch(web if web is not None else WEB), allowed_hosts=set(hosts), max_pages=max_pages)
    model = ScriptedChat(script=list(script))
    agent = build_agent(
        model,
        build_tools(box),
        system_prompt="x",
        checkpointer=saver or InMemorySaver(),
        max_steps=max_steps,
        max_context_tokens=max_tokens,
        on_grant=lambda reason: box.grant_pages(),
        followups=followups,
    )
    return AgentRunner(agent, max_steps=max_steps, base_url="http://lm.test/v1", provider=provider), box, model, pages, rows


async def collect(stream):
    return [event async for event in stream]


def kinds(events):
    return [e.kind for e in events]


def structure(events):
    return [k for k in kinds(events) if k not in ("answer", "reasoning")]


def joined(events, kind):
    return "".join(e.text for e in events if e.kind == kind)


async def test_a_direct_answer_streams_text_and_finishes():
    runner, *_ = setup([ai("Hello there")])
    events = await collect(runner.run("t1", MESSAGES))
    assert kinds(events)[0] == "step" and kinds(events)[-1] == "done"
    assert joined(events, "answer") == "Hello there "


async def test_a_tool_round_trip_emits_steps_tool_events_reasoning_and_urls():
    runner, box, _, pages, _ = setup([ai("", call("fetch_page", "c1", url=URL)), ai("Starter is ten dollars", reasoning="check")])
    events = await collect(runner.run("t1", MESSAGES))
    assert structure(events) == ["step", "tool_start", "tool_result", "step", "done"]
    start = next(e for e in events if e.kind == "tool_start")
    assert (start.name, start.args["url"]) == ("fetch_page", URL)
    result = next(e for e in events if e.kind == "tool_result")
    assert result.ok and result.urls == (URL,) and result.name == "fetch_page"
    assert joined(events, "reasoning") == "check"
    assert len(pages) == 1 and [e.step for e in events if e.kind == "step"] == [1, 2]


async def test_rows_added_events_carry_the_count_and_duplicates_count_zero():
    rows = [{"name": "Starter"}, {"name": "Team"}]
    runner, _, _, _, table = setup([ai("", call("add_rows", "c1", rows=rows)), ai("", call("add_rows", "c2", rows=rows)), ai("ok")])
    events = await collect(runner.run("t1", MESSAGES))
    assert [e.count for e in events if e.kind == "rows_added"] == [2]
    assert table == rows


async def test_bad_model_arguments_become_tool_errors_and_the_run_continues():
    # Review Focus 1: a wrong type is caught by the tool schema, a bad mode by the tool itself
    script = [
        ai("", call("list_links", "c1", url=URL, limit="many")),
        ai("", call("fetch_page", "c2", url=URL, mode="turbo")),
        ai("recovered"),
    ]
    runner, *_ = setup(script)
    events = await collect(runner.run("t1", MESSAGES))
    results = [e for e in events if e.kind == "tool_result"]
    assert [r.ok for r in results] == [False, False]
    assert "fast, dynamic, stealthy" in results[1].text
    assert events[-1].kind == "done"


async def test_ask_user_pauses_and_resuming_with_an_answer_finishes():
    script = [ai("", call("ask_user", "c1", question="Which plan?", options=["Starter", "Team"])), ai("Team it is")]
    runner, *_ = setup(script)
    events = await collect(runner.run("t1", MESSAGES))
    assert events[-1].kind == "needs_input"
    payload = dict(events[-1].payload)
    assert payload.pop("interrupt_id")
    assert payload == {"kind": "ask", "question": "Which plan?", "options": ["Starter", "Team"]}
    assert (await runner.pending("t1"))["kind"] == "ask"
    resumed = await collect(runner.resume("t1", "Team"))
    result = next(e for e in resumed if e.kind == "tool_result")
    assert result.text == "The user answered: Team"
    assert resumed[-1].kind == "done" and await runner.pending("t1") is None


async def test_site_approval_allow_fetches_and_deny_refuses():
    script = [ai("", call("fetch_page", "c1", url=OTHER)), ai("ok")]
    runner, box, _, pages, _ = setup(script)
    paused = await collect(runner.run("allow", MESSAGES))
    assert paused[-1].payload["kind"] == "site" and paused[-1].payload["host"] == "other.test"
    resumed = await collect(runner.resume("allow", {"allow": True}))
    assert resumed[-1].kind == "done" and [p.url for p in pages] == [OTHER] and box.fetched == 1

    runner2, box2, _, pages2, _ = setup(script)
    await collect(runner2.run("deny", MESSAGES))
    denied = await collect(runner2.resume("deny", {"allow": False}))
    assert next(e for e in denied if e.kind == "tool_result").ok is False
    assert pages2 == [] and box2.fetched == 0


async def test_resuming_re_runs_the_tool_step_without_duplicating_work():
    # Review Focus 2: tools are idempotent, so re-running one after a pause, a crash or a Stop never duplicates work
    rows = [{"name": "Starter"}]
    script = [ai("", call("add_rows", "c1", rows=rows), call("fetch_page", "c2", url=OTHER)), ai("ok")]
    runner, box, _, pages, table = setup(script)
    await collect(runner.run("t1", MESSAGES))
    await collect(runner.resume("t1", {"allow": True}))
    assert table == rows and box.fetched == 1 and len(pages) == 1


async def test_a_step_limit_pause_surfaces_and_continue_or_stop_both_work():
    script = [ai("", call("fetch_page", "c1", url=URL)), ai("", call("read_page", "c2", url=URL)), ai("all done")]
    runner, *_ = setup(script, max_steps=2)
    paused = await collect(runner.run("t1", MESSAGES))
    assert paused[-1].kind == "needs_input" and paused[-1].payload["reason"] == "steps"
    finished = await collect(runner.resume("t1", {"action": "continue"}))
    assert finished[-1].kind == "done"

    runner2, *_ = setup(script, max_steps=2)
    await collect(runner2.run("t2", MESSAGES))
    stopped = await collect(runner2.resume("t2", {"action": "stop"}))
    assert stopped[-1].kind == "limit" and stopped[-1].text == "stopped"


async def test_a_context_limit_is_a_terminal_limit_event():
    runner, *_ = setup([ai("never")], max_tokens=50)
    events = await collect(runner.run("t1", [{"role": "user", "content": "y" * 4000}]))
    assert events[-1].kind == "limit" and events[-1].text == "context"


async def test_a_long_run_is_not_cut_off_by_langgraphs_default_recursion_limit():
    urls = [f"https://site.test/p{n}" for n in range(10)]
    web = {u: make_page(u) for u in urls}
    script = [ai("", call("fetch_page", f"c{n}", url=u)) for n, u in enumerate(urls)] + [ai("done")]
    runner, *_ = setup(script, web=web, max_steps=12)
    events = await collect(runner.run("t1", MESSAGES))
    assert events[-1].kind == "done"


async def test_model_connection_errors_are_reported_and_the_run_can_be_resumed():
    error = openai.APIConnectionError(request=httpx.Request("POST", "http://lm.test/v1/chat/completions"))
    runner, *_ = setup([error, ai("fine now")])
    events = await collect(runner.run("t1", MESSAGES))
    assert events[-1].kind == "error" and "Cannot reach LM Studio at http://lm.test/v1" in events[-1].text
    again = await collect(runner.proceed("t1"))
    assert again[-1].kind == "done" and joined(again, "answer") == "fine now "


async def test_errors_name_the_provider_in_use():
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"))
    runner, *_ = setup([error], provider="Mistral")
    events = await collect(runner.run("t1", MESSAGES))
    assert "Cannot reach Mistral at http://lm.test/v1" in events[-1].text
    assert "LM Studio" not in events[-1].text


@pytest.mark.parametrize("status", [401, 403])
async def test_a_rejected_key_is_reported_as_such(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"))
    runner, *_ = setup([openai.AuthenticationError("Unauthorized", response=response, body=None) if status == 401 else openai.PermissionDeniedError("Forbidden", response=response, body=None)], provider="Mistral")
    events = await collect(runner.run("t1", MESSAGES))
    assert events[-1].kind == "error" and "Mistral rejected the API key" in events[-1].text


async def test_an_empty_model_reply_is_an_error():
    runner, *_ = setup([ai("")])
    events = await collect(runner.run("t1", MESSAGES))
    assert events[-1].kind == "error" and "empty answer" in events[-1].text


async def test_leaving_the_stream_early_leaves_a_run_that_proceeds_without_duplicate_work():
    # Review Focus 4: Stop
    runner, box, _, pages, _ = setup([ai("", call("fetch_page", "c1", url=URL)), ai("finished")])
    stream = runner.run("t1", MESSAGES)
    async for event in stream:
        if event.kind == "tool_result":
            break
    await stream.aclose()
    rest = await collect(runner.proceed("t1"))
    assert rest[-1].kind == "done" and box.fetched == 1 and len(pages) == 1


async def test_a_paused_run_survives_a_full_restart(tmp_path):
    # Review Focus 3
    path = tmp_path / "checkpoints.db"
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(path, stack)
        runner, *_ = setup([ai("", call("ask_user", "c1", question="Which plan?"))], saver=saver)
        events = await collect(runner.run("run-1", MESSAGES))
        assert events[-1].kind == "needs_input"
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(path, stack)
        runner, *_ = setup([ai("Team costs forty")], saver=saver)
        assert (await runner.pending("run-1"))["question"] == "Which plan?"
        events = await collect(runner.resume("run-1", "Team"))
        assert events[-1].kind == "done" and joined(events, "answer") == "Team costs forty "


async def test_deleting_a_thread_forgets_its_state():
    runner, *_ = setup([ai("", call("ask_user", "c1", question="Which?"))])
    await collect(runner.run("t1", MESSAGES))
    await runner.delete("t1")
    assert await runner.pending("t1") is None


async def test_an_unreadable_checkpoint_file_is_set_aside_and_replaced(tmp_path):
    # Review Focus 8
    path = tmp_path / "checkpoints.db"
    path.write_bytes(b"not a database" * 100)
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(path, stack)
        runner, *_ = setup([ai("fine")], saver=saver)
        events = await collect(runner.run("t1", MESSAGES))
        assert events[-1].kind == "done"
    assert list(tmp_path.glob("checkpoints.db.corrupt-*"))


async def test_a_missing_data_directory_is_created_for_the_checkpoint_file(tmp_path):
    async with AsyncExitStack() as stack:
        await open_checkpointer(tmp_path / "new" / "checkpoints.db", stack)
    assert (tmp_path / "new" / "checkpoints.db").exists()


def last_pause(events):
    """The pending question of a stream. Not simply the last event: LangGraph may emit a sibling tool result after it."""
    return next((e for e in reversed(events) if e.kind == "needs_input"), None)


async def test_two_pauses_in_one_step_are_answered_one_at_a_time_by_interrupt_id():
    # Qwen issues parallel tool calls: two unlisted sites pause at once
    a, b = "https://a.test/x", "https://b.test/y"
    web = {a: make_page(a), b: make_page(b)}
    runner, box, _, pages, _ = setup([ai("", call("fetch_page", "c1", url=a), call("fetch_page", "c2", url=b)), ai("done")], web=web)
    events = await collect(runner.run("t1", MESSAGES))
    hosts = []
    while (pause := last_pause(events)) is not None:
        hosts.append(pause.payload["host"])
        events = await collect(runner.resume("t1", {"allow": len(hosts) == 1}, interrupt_id=pause.payload["interrupt_id"]))
    assert events[-1].kind == "done" and sorted(hosts) == ["a.test", "b.test"]
    assert [host_of(page.url) for page in pages] == [hosts[0]]  # only the first answer was Allow


async def test_a_mixed_ask_and_site_pause_keeps_each_answer_with_its_question():
    a = "https://a.test/x"
    script = [ai("", call("ask_user", "c1", question="Which?"), call("fetch_page", "c2", url=a)), ai("done")]
    runner, box, _, pages, _ = setup(script, web={a: make_page(a)})
    events = await collect(runner.run("t1", MESSAGES))
    answers = {"ask": "Team", "site": {"allow": True}}
    while (pause := last_pause(events)) is not None:
        events = await collect(runner.resume("t1", answers[pause.payload["kind"]], interrupt_id=pause.payload["interrupt_id"]))
    tool_results = [e.text for e in events if e.kind == "tool_result"]
    assert events[-1].kind == "done" and "The user answered: Team" in " ".join(tool_results) and len(pages) == 1


async def test_a_final_reply_made_only_of_invalid_tool_calls_is_a_final_error_with_a_clear_message():
    error = {"name": "fetch_page", "args": "nope", "id": "c1", "error": "bad", "type": "invalid_tool_call"}
    bad = AIMessage(content="", invalid_tool_calls=[error])
    runner, *_ = setup([bad, bad, bad, bad])
    events = await collect(runner.run("t1", MESSAGES))
    assert events[-1].kind == "error" and "valid JSON" in events[-1].text and events[-1].payload == {"final": True}


async def test_an_empty_final_reply_is_marked_final_so_no_resume_is_offered():
    runner, *_ = setup([ai("")])
    events = await collect(runner.run("t1", MESSAGES))
    assert events[-1].kind == "error" and events[-1].payload == {"final": True}


async def test_a_delivered_follow_up_is_reported_as_an_event():
    calls = {"n": 0}

    def pending():
        calls["n"] += 1
        return [] if calls["n"] == 1 else [{"id": "f1", "text": "also the SKU", "delivered": False}]

    runner, *_ = setup([ai("", call("fetch_page", "c1", url=URL)), ai("done")], followups=pending)
    events = await collect(runner.run("t", MESSAGES))
    assert [(e.id, e.text) for e in events if e.kind == "followup"] == [("f1", "also the SKU")]
    assert events[-1].kind == "done"
