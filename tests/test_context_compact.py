"""When the conversation outgrows the model's context, older work is summarised by the model and the recent steps stay word for word."""

import pytest
from fakes import ScriptedChat, ai, call, make_fetch, make_page
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from scraper.agent import AgentRunner, build_agent
from scraper.tools import ToolBox, build_tools

MESSAGES = [{"role": "user", "content": "Collect every plan from site.test"}]
SUMMARY = "Progress notes: fetched pages 1 to 3, Starter is ten dollars, still to do: page 4."


def big(n):
    return make_page(f"https://site.test/p{n}", title=f"Page {n}", markdown=f"# Page {n}\n\n" + (f"unique-marker-{n} " + "filler words here " * 60))


WEB = {f"https://site.test/p{n}": big(n) for n in range(1, 9)}


def fetch(n):
    return ai("", call("fetch_page", f"c{n}", url=f"https://site.test/p{n}"))


def runner_for(script, *, max_tokens=900, compact=True, charges=None, followups=None):
    box = ToolBox(pages=[], rows=[], fetch_fn=make_fetch(WEB), allowed_hosts={"site.test"}, max_pages=100, result_tokens=800)
    model = ScriptedChat(script=list(script))
    agent = build_agent(
        model,
        build_tools(box),
        system_prompt="x",
        checkpointer=InMemorySaver(),
        max_steps=25,
        max_context_tokens=max_tokens,
        usage_charge=(lambda: charges.append(1)) if charges is not None else None,
        followups=followups,
        compact=compact,
    )
    return AgentRunner(agent, max_steps=25), model


async def run(runner):
    return [event async for event in runner.run("t", MESSAGES)]


def agent_calls(model):
    """The model calls the agent made (it has tools bound), as opposed to the summariser's call (it has none)."""
    return [c for c in model.calls if c["tools"]]


def summariser_calls(model):
    return [c for c in model.calls if not c["tools"]]


def text_of(messages):
    return "\n".join(str(m.content) for m in messages)


async def test_nothing_is_summarised_while_the_conversation_fits():
    runner, model = runner_for([fetch(1), ai("Done.")], max_tokens=50_000)
    await run(runner)
    assert summariser_calls(model) == []


async def test_old_work_is_replaced_by_the_models_own_summary_and_the_recent_steps_stay_whole():
    runner, model = runner_for([fetch(1), fetch(2), fetch(3), ai(SUMMARY), ai("All done.")])
    events = await run(runner)
    assert [e.kind for e in events][-1] == "done"
    assert len(summariser_calls(model)) == 1
    after = text_of(agent_calls(model)[-1]["messages"])
    assert SUMMARY in after and "Collect every plan from site.test" in after  # the instruction is never summarised away
    assert "unique-marker-3" in after and "unique-marker-2" in after  # the last two steps are word for word
    assert "unique-marker-1" not in after and "[trimmed to save context]" not in after


async def test_the_messages_that_remain_are_still_a_valid_conversation():
    runner, model = runner_for([fetch(1), fetch(2), fetch(3), ai(SUMMARY), ai("All done.")])
    await run(runner)
    seen = set()
    for message in agent_calls(model)[-1]["messages"]:
        for tool_call in getattr(message, "tool_calls", None) or []:
            seen.add(tool_call["id"])
        if isinstance(message, ToolMessage):
            assert message.tool_call_id in seen, "a tool result whose call was summarised away"


async def test_what_the_summariser_is_asked_names_what_must_survive():
    runner, model = runner_for([fetch(1), fetch(2), fetch(3), ai(SUMMARY), ai("All done.")])
    await run(runner)
    asked = text_of(summariser_calls(model)[0]["messages"]).lower()
    for must in ("instruction", "url", "rows", "answers the user gave", "remains"):
        assert must in asked
    assert "unique-marker-1" in asked  # it is given the old work to summarise


async def test_the_summary_is_not_shown_to_the_person_as_part_of_the_answer():
    runner, _ = runner_for([fetch(1), fetch(2), fetch(3), ai(SUMMARY), ai("All done.")])
    events = await run(runner)
    shown = "".join(e.text for e in events if e.kind in ("answer", "reasoning"))
    assert "Progress notes" not in shown and "All done." in shown


async def test_the_summary_costs_a_model_call_of_the_daily_allowance():
    charges: list = []
    runner, model = runner_for([fetch(1), fetch(2), fetch(3), ai(SUMMARY), ai("All done.")], charges=charges)
    await run(runner)
    assert len(charges) == len(model.calls)  # each agent call and the summary


async def test_a_second_summary_includes_the_first():
    script = [fetch(1), fetch(2), fetch(3), ai(SUMMARY), fetch(4), ai("Second summary: also pages 2 to 4."), ai("All done.")]
    runner, model = runner_for(script)
    await run(runner)
    assert len(summariser_calls(model)) == 2
    assert "Progress notes" in text_of(summariser_calls(model)[1]["messages"])  # the notes so far are carried forward, not lost


async def test_if_the_summary_fails_it_falls_back_to_trimming_and_the_run_carries_on():
    runner, model = runner_for([fetch(1), fetch(2), fetch(3), RuntimeError("model unavailable"), ai("All done.")])
    events = await run(runner)
    assert [e.kind for e in events][-1] == "done"
    assert "[trimmed to save context]" in text_of(agent_calls(model)[-1]["messages"])


async def test_when_even_a_summary_leaves_too_much_the_run_ends_cleanly_as_before():
    runner, _ = runner_for([fetch(1), fetch(2), fetch(3), ai(SUMMARY)], max_tokens=300)
    events = await run(runner)
    assert [e.kind for e in events][-1] == "limit" and events[-1].text == "context"


async def test_with_compaction_off_old_results_are_trimmed_as_before():
    runner, model = runner_for([fetch(1), fetch(2), fetch(3), ai("All done.")], compact=False)
    await run(runner)
    assert summariser_calls(model) == [] and "[trimmed to save context]" in text_of(agent_calls(model)[-1]["messages"])


async def test_a_follow_up_that_is_summarised_is_not_announced_again():
    inbox = {"items": []}

    def pending():
        items, inbox["items"] = inbox["items"], []
        return items

    # a follow-up delivered early in the run, then enough work for it to be summarised
    runner, model = runner_for([fetch(1), fetch(2), fetch(3), ai(SUMMARY), ai("All done.")], followups=pending)
    inbox["items"] = [{"id": "f1", "text": "also grab the SKU", "delivered": False}]
    events = await run(runner)
    assert [e.kind for e in events].count("followup") == 1
    assert "also grab the SKU" in text_of(summariser_calls(model)[0]["messages"])  # it is in what was summarised, so the notes can carry it
