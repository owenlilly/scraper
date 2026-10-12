"""A long run must end because of the agent's own step limit (which asks the person what to do), never because of LangGraph's recursion limit."""

from fakes import ScriptedChat, ai, call, make_fetch, make_page
from langgraph.checkpoint.memory import InMemorySaver

from scraper.agent import AgentRunner, build_agent, describe_error
from scraper.tools import ToolBox, build_tools

URL = "https://site.test/p"
MESSAGES = [{"role": "user", "content": "go"}]


def runner_for(script, max_steps):
    box = ToolBox(pages=[], rows=[], fetch_fn=make_fetch({URL: make_page(URL)}), allowed_hosts={"site.test"}, max_pages=1000)
    model = ScriptedChat(script=list(script))
    agent = build_agent(
        model,
        build_tools(box),
        system_prompt="x",
        checkpointer=InMemorySaver(),
        max_steps=max_steps,
        max_context_tokens=1_000_000,
        usage_charge=lambda: None,  # the middleware a hosted run has, all of it: every one adds steps to the graph
        followups=lambda: [],
    )
    return AgentRunner(agent, max_steps=max_steps)


def distinct_calls(count):
    """Tool calls that differ from each other, so the repeat guard (three identical calls in a row) never fires."""
    return [ai("", call("list_links", f"c{i}", url=URL, limit=i + 1)) for i in range(count)]


async def collect(stream):
    return [event async for event in stream]


async def test_a_run_of_the_full_length_finishes_without_hitting_the_graph_limit():
    runner = runner_for([*distinct_calls(24), ai("All done.")], max_steps=25)
    events = await collect(runner.run("t", MESSAGES))
    assert [e.kind for e in events][-1] == "done", [e.text for e in events if e.kind == "error"]


async def test_the_step_limit_still_asks_the_person_when_it_is_reached():
    runner = runner_for(distinct_calls(40), max_steps=25)
    events = await collect(runner.run("t", MESSAGES))
    kinds = [e.kind for e in events]
    assert "needs_input" in kinds and "error" not in kinds
    pending = next(e for e in events if e.kind == "needs_input")
    assert pending.payload["kind"] == "limit" and pending.payload["reason"] == "steps"


def test_if_the_graph_limit_is_ever_hit_the_person_is_told_in_plain_words_and_that_their_work_is_saved():
    from langgraph.errors import GraphRecursionError

    text = describe_error(GraphRecursionError("Recursion limit of 125 reached"), "http://x/v1")
    assert "125" not in text and "Resume" in text and "saved" in text
