from datetime import date

from fakes import FakeLLM, ScriptedChat, ai, call, make_fetch, make_page
from langgraph.checkpoint.memory import InMemorySaver

from scraper.agent import AgentRunner, build_agent, describe_error
from scraper.config import Config
from scraper.llm import Event
from scraper.session import Session
from scraper.tools import ToolBox, build_tools
from scraper.usage import MemoryUsageStore, UsageLimitReached, UsageMeter

URL = "https://site.test/pricing"
WEB = {URL: make_page(URL, markdown="# Pricing\n\nStarter ten dollars.")}
MESSAGES = [{"role": "user", "content": "go"}]


class Clock:
    def __init__(self):
        self.day = date(2026, 10, 9)

    def __call__(self):
        return self.day


def agent_with_meter(script, *, model_calls):
    clock = Clock()
    meter = UsageMeter("u1", MemoryUsageStore(), Config(daily_model_calls=model_calls), today=clock)
    box = ToolBox(pages=[], rows=[], fetch_fn=make_fetch(WEB), allowed_hosts={"site.test"})
    agent = build_agent(
        ScriptedChat(script=list(script)),
        build_tools(box),
        system_prompt="x",
        checkpointer=InMemorySaver(),
        max_steps=25,
        max_context_tokens=100_000,
        usage_charge=meter.charge_model_call,
    )
    return AgentRunner(agent, max_steps=25, base_url="http://x/v1", provider="Mistral"), meter, clock


async def collect(stream):
    return [event async for event in stream]


async def test_a_spent_allowance_ends_the_round_with_the_plain_message_and_the_thread_resumes_tomorrow():
    runner, meter, clock = agent_with_meter([ai("", call("fetch_page", url=URL)), ai("Starter is ten dollars.")], model_calls=1)
    first = await collect(runner.run("t1", MESSAGES))
    errors = [e for e in first if e.kind == "error"]
    assert len(errors) == 1 and "model-call limit" in errors[0].text and "UsageLimitReached" not in errors[0].text
    assert any(e.kind == "tool_result" for e in first)  # the first call and its tool ran before the limit bit
    clock.day = date(2026, 10, 10)  # the next UTC day
    second = await collect(runner.proceed("t1"))
    assert second[-1].kind == "done"


async def test_every_model_call_is_charged_once():
    runner, meter, _ = agent_with_meter([ai("", call("fetch_page", url=URL)), ai("done now")], model_calls=10)
    await collect(runner.run("t1", MESSAGES))
    assert meter.snapshot()["model_calls"] == 2


def test_describe_error_shows_a_usage_limit_message_as_written():
    assert describe_error(UsageLimitReached("Daily page limit reached (5 a day)."), "http://x", "Mistral") == "Daily page limit reached (5 a day)."


async def test_ask_is_charged_and_refused_at_the_limit_without_calling_the_model():
    meter = UsageMeter("u1", MemoryUsageStore(), Config(daily_model_calls=1), today=Clock())
    llm = FakeLLM(events=[Event("answer", "Ten dollars.")])
    session = Session(Config(request_delay=0.0), llm, make_fetch(WEB), usage=meter)
    await session.scrape(URL)
    first = [e async for e in session.ask("price?", "qwen-test")]
    assert first[-1] == Event("answer", "Ten dollars.")
    second = [e async for e in session.ask("again?", "qwen-test")]
    assert [e.kind for e in second] == ["error"] and "model-call limit" in second[0].text
    assert len(llm.calls) == 1  # the refused question never reached the model
    assert len(session.history) == 2  # nor was it recorded


async def test_a_question_that_fails_before_the_model_is_not_charged():
    meter = UsageMeter("u1", MemoryUsageStore(), Config(daily_model_calls=5), today=Clock())
    session = Session(Config(request_delay=0.0), FakeLLM(), make_fetch(WEB), usage=meter)
    events = [e async for e in session.ask("price?", "qwen-test")]  # no page scraped yet
    assert events[0].kind == "error" and meter.snapshot()["model_calls"] == 0
