"""Asking a question with no pages read yet: the person may want to know what the app can do, so the model is asked, and told."""

from fakes import FakeLLM, make_fetch, make_page
from scraper.config import Config
from scraper.llm import Event
from scraper.session import Session
from scraper.tools import tool_summaries

A = "https://site.test/a"


def session(cfg=None, answer="Here is what I can do."):
    llm = FakeLLM([Event("answer", answer)])
    return Session(cfg or Config(), llm, make_fetch({A: make_page(A)}, [])), llm


async def ask(s, question="What tools do you have?"):
    return [event async for event in s.ask(question, "qwen-test")]


async def test_a_question_with_no_pages_is_answered_by_the_model_not_refused():
    s, llm = session()
    events = await ask(s)
    assert [e.kind for e in events] == ["answer"] and len(llm.calls) == 1


async def test_the_model_is_told_no_pages_have_been_read_and_what_the_app_can_do():
    s, llm = session()
    await ask(s)
    system = llm.calls[0][0]["content"]
    assert "No pages have been read yet" in system
    for name in ("fetch_page", "add_rows", "browser_click", "browser_network"):
        assert name in system
    assert "what this app can do" in system.lower()


async def test_the_list_follows_the_browser_setting():
    s, llm = session(Config(browser=False))
    await ask(s)
    system = llm.calls[0][0]["content"]
    assert "fetch_page" in system and "browser_click" not in system


async def test_the_list_is_given_when_there_are_pages_too():
    s, llm = session()
    await s.scrape(A)
    await ask(s, "Which tools can you use?")
    assert "browser_scroll" in llm.calls[0][0]["content"] and "Page content:" in llm.calls[0][0]["content"]


async def test_the_question_is_kept_in_the_chat_and_there_are_no_sources():
    s, _ = session()
    await ask(s)
    assert [m.role for m in s.history] == ["user", "assistant"] and s.history[1].sources == ()


def test_every_tool_has_a_one_line_summary_in_plain_words():
    with_browser, without = tool_summaries(True, True), tool_summaries(False, True)
    assert [name for name, _ in with_browser][:3] == ["fetch_page", "web_search", "browser_submit_form"]
    assert not any(name.startswith("browser_") for name, _ in without) and len(without) == 8 and len(with_browser) == 12
    assert "web_search" not in [name for name, _ in tool_summaries(True, False)] and len(tool_summaries(True, False)) == 11
    assert all(text and "\n" not in text and len(text) < 160 for _, text in with_browser)
