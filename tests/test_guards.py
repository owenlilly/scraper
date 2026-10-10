from fakes import ScriptedChat, ai, call
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from scraper.guards import TRIMMED, ContextTrim, FollowupInbox, RunGuard

probed: list[str] = []


@tool(response_format="content_and_artifact")
def probe(url: str) -> tuple[str, dict]:
    """Probe a URL."""
    probed.append(url)
    return f"probed {url}", {"ok": True, "fetched": [url], "rows_added": 0, "limit": None}


@tool(response_format="content_and_artifact")
def exhaust(url: str) -> tuple[str, dict]:
    """Report that the page budget is used up."""
    return "budget used up", {"ok": False, "fetched": [], "rows_added": 0, "limit": "pages"}


@tool(response_format="content_and_artifact")
def big(url: str) -> tuple[str, dict]:
    """Return about a thousand tokens."""
    return "x" * 4000, {"ok": True, "fetched": [], "rows_added": 0, "limit": None}


def build(script, *, tools=(probe,), max_steps=25, max_tokens=100_000, on_grant=None):
    probed.clear()
    model = ScriptedChat(script=list(script))
    agent = create_agent(
        model,
        tools=list(tools),
        system_prompt="x",
        checkpointer=InMemorySaver(),
        middleware=[ContextTrim(max_tokens), RunGuard(max_steps=max_steps, on_grant=on_grant)],
    )
    return agent, model


def first(text="go"):
    return {"messages": [{"role": "user", "content": text}]}


async def drive(agent, payload, thread="t"):
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 100}
    pause = None
    async for chunk in agent.astream(payload, config, stream_mode="updates"):
        if "__interrupt__" in chunk:
            pause = chunk["__interrupt__"][0].value
    return pause, await agent.aget_state(config)


def resume(**decision):
    return Command(resume=decision)


STEPS_SCRIPT = [ai("", call("probe", "c1", url="a")), ai("", call("probe", "c2", url="b")), ai("", call("probe", "c3", url="c")), ai("All done")]


async def test_the_step_limit_pauses_and_continue_grants_another_allowance():
    agent, _ = build(STEPS_SCRIPT, max_steps=2)
    pause, state = await drive(agent, first())
    assert pause["kind"] == "limit" and pause["reason"] == "steps"
    assert [a["action"] for a in pause["actions"]] == ["continue", "wrap_up", "stop"]
    assert state.next and state.values["steps"] == 2 and probed == ["a", "b"]
    pause, state = await drive(agent, resume(action="continue"))
    assert pause is None and state.next == ()
    assert state.values["messages"][-1].content == "All done" and state.values["allowance"] == 4
    assert probed == ["a", "b", "c"]  # nothing re-ran after the resume


async def test_wrap_up_makes_the_next_model_call_tool_less():
    script = [ai("", call("probe", "c1", url="a")), ai("", call("probe", "c2", url="b")), ai("Wrapped answer")]
    agent, model = build(script, max_steps=2)
    await drive(agent, first())
    _, state = await drive(agent, resume(action="wrap_up", note="keep it short"))
    assert state.values["messages"][-1].content == "Wrapped answer"
    assert model.calls[-1]["tools"] == []
    last_human = [m for m in state.values["messages"] if isinstance(m, HumanMessage)][-1]
    assert "Do not call any more tools" in last_human.content and "keep it short" in last_human.content


async def test_stop_and_unknown_actions_end_the_run_without_another_model_call():
    for decision in (resume(action="stop"), resume(action="dance"), Command(resume="just stop")):
        agent, model = build(STEPS_SCRIPT, max_steps=2)
        await drive(agent, first())
        before = len(model.calls)
        _, state = await drive(agent, decision)
        assert state.next == () and state.values["end_reason"] == "stopped"
        assert len(model.calls) == before


async def test_a_continue_note_reaches_the_model():
    agent, model = build(STEPS_SCRIPT, max_steps=2)
    await drive(agent, first())
    await drive(agent, resume(action="continue", note="focus on pricing"))
    humans = [m.content for m in model.calls[-1]["messages"] if isinstance(m, HumanMessage)]
    assert any("focus on pricing" in text for text in humans)


async def test_the_page_budget_limit_pauses_and_continue_calls_the_grant_hook():
    granted = []
    script = [ai("", call("exhaust", "c1", url="a")), ai("final answer")]
    agent, _ = build(script, tools=(exhaust,), on_grant=granted.append)
    pause, _ = await drive(agent, first())
    assert pause["reason"] == "pages"
    _, state = await drive(agent, resume(action="continue"))
    assert granted == ["pages"] and state.values["messages"][-1].content == "final answer"


async def test_the_same_call_three_times_pauses_with_a_repeat_question():
    # Review Focus 4
    same = [ai("", call("probe", f"c{n}", url="a")) for n in range(3)]
    agent, _ = build(same + [ai("done")])
    pause, state = await drive(agent, first())
    assert pause["reason"] == "repeat" and probed == ["a", "a", "a"]
    _, state = await drive(agent, resume(action="continue", note="try b"))
    assert state.next == () and state.values["messages"][-1].content == "done"
    assert state.values["streak"] == 0


async def test_different_or_interleaved_calls_do_not_pause():
    urls = ["a", "a", "b", "a", "a"]
    script = [ai("", call("probe", f"c{n}", url=u)) for n, u in enumerate(urls)] + [ai("done")]
    agent, _ = build(script)
    pause, state = await drive(agent, first())
    assert pause is None and state.next == ()


async def test_old_tool_results_are_trimmed_when_the_conversation_grows():
    script = [ai("", call("big", f"c{n}", url=str(n))) for n in range(4)] + [ai("done")]
    agent, model = build(script, tools=(big,), max_tokens=2500)
    _, state = await drive(agent, first())
    assert state.values["messages"][-1].content == "done"
    contents = [m.content for m in model.calls[-1]["messages"] if isinstance(m, ToolMessage)]
    assert contents[:2] == [TRIMMED, TRIMMED] and contents[2:] == ["x" * 4000, "x" * 4000]


async def test_a_conversation_that_cannot_be_trimmed_to_fit_ends_with_a_context_limit():
    agent, model = build([ai("never")], max_tokens=100)
    _, state = await drive(agent, first("y" * 4000))
    assert state.values["end_reason"] == "context" and model.calls == []


def bad_call(n):
    error = {"name": "probe", "args": "not json at all", "id": f"c{n}", "error": "bad", "type": "invalid_tool_call"}
    return AIMessage(content="", invalid_tool_calls=[error])


async def test_invalid_tool_call_json_is_sent_back_to_the_model_to_correct():
    # Review Focus 1: create_agent would otherwise treat it as an empty final answer
    agent, model = build([bad_call(1), ai("", call("probe", "c2", url="a")), ai("fixed")])
    _, state = await drive(agent, first())
    assert state.values["messages"][-1].content == "fixed" and probed == ["a"]
    humans = [m.content for m in model.calls[1]["messages"] if isinstance(m, HumanMessage)]
    assert any("not valid JSON" in text for text in humans)


async def test_repeated_invalid_tool_calls_stop_instead_of_looping():
    agent, model = build([bad_call(n) for n in range(1, 6)])
    _, state = await drive(agent, first())
    assert state.next == () and len(model.calls) == 3  # the first call plus two corrections, then it gives up


queue: list[dict] = []


@tool(response_format="content_and_artifact")
def probe_then_queue(url: str) -> tuple[str, dict]:
    """Probe a URL while the user types a follow-up."""
    queue.append({"id": "f1", "text": "also the SKU", "delivered": False})
    return f"probed {url}", {"ok": True, "fetched": [url], "rows_added": 0, "limit": None}


def build_with_inbox(script, pending, *, tools=(probe,), injected=None, present=None):
    model = ScriptedChat(script=list(script))
    agent = create_agent(
        model,
        tools=list(tools),
        system_prompt="x",
        checkpointer=InMemorySaver(),
        middleware=[
            ContextTrim(100_000),
            RunGuard(max_steps=25),
            FollowupInbox(
                pending,
                on_inject=injected.append if injected is not None else None,
                on_present=present.append if present is not None else None,
            ),
        ],
    )
    return agent, model


def humans(call_record):
    return [m.content for m in call_record["messages"] if isinstance(m, HumanMessage)]


async def test_a_follow_up_queued_during_a_tool_call_is_injected_exactly_once_before_the_next_model_call():
    queue.clear()
    injected = []
    script = [ai("", call("probe_then_queue", "c1", url="a")), ai("done")]
    agent, model = build_with_inbox(script, lambda: list(queue), tools=(probe_then_queue,), injected=injected)
    _, state = await drive(agent, first())
    assert "also the SKU" not in humans(model.calls[0])
    assert humans(model.calls[1])[-1] == "also the SKU"
    sent = [m for m in state.values["messages"] if m.additional_kwargs.get("followup")]
    assert [(m.id, m.content) for m in sent] == [("f1", "also the SKU")]  # the flag was never set, yet no second copy
    assert [f["id"] for f in injected] == ["f1"] and len(model.calls) == 2


async def test_a_follow_up_arriving_with_the_final_answer_sends_the_run_back_to_the_model():
    calls = {"n": 0}

    def pending():
        calls["n"] += 1
        return [] if calls["n"] == 1 else [{"id": "f1", "text": "one more thing", "delivered": False}]

    agent, model = build_with_inbox([ai("First answer."), ai("Second answer.")], pending)
    _, state = await drive(agent, first())
    assert len(model.calls) == 2 and humans(model.calls[1])[-1] == "one more thing"
    assert state.values["messages"][-1].content == "Second answer."


async def test_a_reply_with_unparseable_tool_calls_is_corrected_not_mistaken_for_a_final_answer():
    calls = {"n": 0}

    def pending():
        calls["n"] += 1
        return [] if calls["n"] == 1 else [{"id": "f1", "text": "ignore me", "delivered": False}]

    agent, model = build_with_inbox([bad_call(1), ai("", call("probe", "c2", url="a")), ai("fixed")], pending)
    _, state = await drive(agent, first())
    assert state.values["messages"][-1].content == "fixed" and len(model.calls) == 3
    second = humans(model.calls[1])
    assert any("not valid JSON" in text for text in second) and second[-1] == "ignore me"


async def test_a_follow_up_already_in_the_conversation_is_reported_as_present_when_its_flag_is_unset():
    queue.clear()
    present = []
    script = [ai("", call("probe_then_queue", "c1", url="a")), ai("done")]
    agent, _ = build_with_inbox(script, lambda: list(queue), tools=(probe_then_queue,), present=present)
    await drive(agent, first())
    assert {f["id"] for f in present} == {"f1"}  # the state has it, the flag says otherwise: the owner reconciles the flag
