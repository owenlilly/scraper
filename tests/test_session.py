from dataclasses import replace

import pytest

from fakes import FakeLLM, make_fetch, make_page
from scraper.config import Config
from scraper.fetch import FetchError
from scraper.llm import Event
from scraper.session import SYSTEM_PROMPT, Message, Session

A, B, C = "https://site.test/a", "https://site.test/b", "https://site.test/c"
CFG = Config(request_delay=0.0)


def make_session(web, llm=None, cfg=CFG, calls=None):
    return Session(cfg, llm or FakeLLM(), make_fetch(web, calls))


async def ask_all(session, question="q?", model="qwen-test"):
    return [event async for event in session.ask(question, model)]


# ---- scrape -----------------------------------------------------------------


async def test_scrape_single_page():
    web = {A: make_page(A, links=[B]), B: make_page(B)}
    session = make_session(web)
    await session.scrape(A, "fast")
    assert [p.url for p in session.pages] == [A]
    assert session.history == []


async def test_scrape_follow_links_passes_mode_and_reports_progress():
    web = {A: make_page(A, links=[B, C]), B: make_page(B), C: make_page(C)}
    calls = []
    session = make_session(web, calls=calls)
    await session.scrape(A, "dynamic", follow=True, depth=1)
    assert [p.url for p in session.pages] == [A, B, C]
    assert {mode for _, mode in calls} == {"dynamic"}
    assert session.progress == (3, 3)


async def test_failed_scrape_keeps_the_previous_session():
    web = {A: make_page(A)}
    session = make_session(web)
    await session.scrape(A)
    session.history.extend([Message("user", "hi"), Message("assistant", "yo")])
    with pytest.raises(FetchError):
        await session.scrape("https://site.test/missing")
    assert [p.url for p in session.pages] == [A]
    assert len(session.history) == 2


async def test_new_scrape_clears_history():
    web = {A: make_page(A), B: make_page(B)}
    llm = FakeLLM([Event("answer", "x")])
    session = make_session(web, llm)
    await session.scrape(A)
    await ask_all(session)
    assert len(session.history) == 2
    await session.scrape(B)
    assert session.history == []
    assert session.last_context is None


# ---- ask --------------------------------------------------------------------


async def test_ask_streams_events_records_history_and_builds_the_prompt():
    web = {A: make_page(A, markdown="# Pricing\n\nTen dollars.")}
    llm = FakeLLM([Event("reasoning", "think"), Event("answer", "Hel"), Event("answer", "lo")])
    session = make_session(web, llm)
    await session.scrape(A)
    events = await ask_all(session, "How much?")
    assert [e.kind for e in events] == ["reasoning", "answer", "answer"]
    user, assistant = session.history
    assert (user.role, user.content) == ("user", "How much?")
    assert (assistant.role, assistant.content, assistant.reasoning) == ("assistant", "Hello", "think")
    assert assistant.sources == (A,) and not assistant.interrupted
    system, last = llm.calls[0][0], llm.calls[0][-1]
    assert system["role"] == "system" and SYSTEM_PROMPT in system["content"] and "Ten dollars." in system["content"]
    assert last == {"role": "user", "content": "How much?"}
    assert session.last_context.mode == "full"


async def test_ask_without_pages_is_an_error_and_never_calls_the_model():
    llm = FakeLLM([Event("answer", "x")])
    session = make_session({}, llm)
    events = await ask_all(session)
    assert [e.kind for e in events] == ["error"]
    assert llm.calls == []


async def test_pages_without_readable_text_are_reported_not_sent():
    # Review Focus 3
    web = {A: make_page(A, markdown="  ")}
    llm = FakeLLM([Event("answer", "x")])
    session = make_session(web, llm)
    await session.scrape(A)
    events = await ask_all(session)
    assert events[0].kind == "error" and "no readable text" in events[0].text
    assert llm.calls == [] and session.history == []


async def test_error_after_partial_answer_keeps_it_as_interrupted_and_can_be_regenerated():
    # Review Focus 5
    web = {A: make_page(A)}
    llm = FakeLLM([Event("answer", "Par"), Event("error", "stream died")])
    session = make_session(web, llm)
    await session.scrape(A)
    events = await ask_all(session, "Why?")
    assert events[-1].kind == "error"
    assert session.history[-1].interrupted and session.history[-1].content == "Par"
    assert session.pop_interrupted() == "Why?"
    assert session.history == []
    assert session.pop_interrupted() is None


async def test_error_with_no_answer_leaves_history_unchanged():
    # Review Focus 5
    web = {A: make_page(A)}
    session = make_session(web, FakeLLM([Event("error", "down")]))
    await session.scrape(A)
    await ask_all(session)
    assert session.history == []


async def test_empty_model_answer_is_reported_and_not_recorded():
    # Review Focus 5
    web = {A: make_page(A)}
    session = make_session(web, FakeLLM([]))
    await session.scrape(A)
    events = await ask_all(session)
    assert events[0].kind == "error" and "empty answer" in events[0].text
    assert session.history == []


async def test_only_reasoning_counts_as_an_empty_answer():
    web = {A: make_page(A)}
    session = make_session(web, FakeLLM([Event("reasoning", "hmm")]))
    await session.scrape(A)
    events = await ask_all(session)
    assert events[-1].kind == "error"
    assert session.history == []


async def test_small_context_window_switches_to_retrieval():
    filler = "lorem ipsum dolor sit amet " * 110
    md = "".join(f"# Section {n}\n\n{filler}\n\n" for n in range(10))
    web = {A: make_page(A, markdown=md)}
    llm = FakeLLM([Event("answer", "ok")], window=1000)
    session = make_session(web, llm)
    await session.scrape(A)
    await ask_all(session, "lorem")
    assert session.last_context.mode == "retrieved"


async def test_history_is_trimmed_oldest_first_within_the_reserve():
    web = {A: make_page(A)}
    llm = FakeLLM([Event("answer", "x" * 80)])  # about 20 tokens per answer
    session = make_session(web, llm, cfg=replace(CFG, history_reserve=50))
    await session.scrape(A)
    for n in range(3):
        await ask_all(session, f"question {n}")
    await ask_all(session, "question 3")
    roles = [m["role"] for m in llm.calls[-1]]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    contents = " ".join(m["content"] for m in llm.calls[-1])
    assert "question 0" not in contents and "question 2" in contents


async def test_interrupted_exchanges_are_not_replayed_to_the_model():
    web = {A: make_page(A)}
    llm = FakeLLM([Event("answer", "Par"), Event("error", "died")])
    session = make_session(web, llm)
    await session.scrape(A)
    await ask_all(session, "first")
    llm.events = [Event("answer", "fine")]
    await ask_all(session, "second")
    assert [m["role"] for m in llm.calls[-1]] == ["system", "user"]
    assert llm.calls[-1][-1]["content"] == "second"
