from contextlib import AsyncExitStack
from dataclasses import replace

import httpx
import openai
import pytest

from fakes import FakeLLM, ScriptedChat, ai, call, make_fetch, make_page
from scraper.agent import open_checkpointer
from scraper.config import Config
from scraper.context import estimate_tokens
from scraper.fetch import FetchError
from scraper.llm import Event
from scraper.session import SYSTEM_PROMPT, Message, Session, SessionBusy
from scraper.store import SessionStore
from scraper.tools import host_of

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


async def test_ask_reports_whether_it_recorded_an_interrupted_answer():
    web = {A: make_page(A)}
    llm = FakeLLM([Event("answer", "Par"), Event("error", "died")])
    session = make_session(web, llm)
    await session.scrape(A)
    await ask_all(session, "first")
    assert session.last_ask_interrupted is True
    llm.events = [Event("error", "down")]
    await ask_all(session, "second")
    assert session.last_ask_interrupted is False
    assert [m.content for m in session.history] == ["first", "Par"]


async def test_prompt_plus_reply_reserve_fits_the_window_even_with_history():
    filler = "lorem ipsum dolor sit amet " * 110
    md = "".join(f"# Section {n}\n\n{filler}\n\n" for n in range(10))
    web = {A: make_page(A, markdown=md)}
    llm = FakeLLM([Event("answer", "x" * 1980)], window=8192)
    session = make_session(web, llm)
    await session.scrape(A)
    for n in range(7):
        await ask_all(session, f"lorem q{n}")
    prompt_tokens = sum(estimate_tokens(m["content"]) for m in llm.calls[-1])
    assert prompt_tokens + CFG.reply_reserve <= 8192


# ---- providers --------------------------------------------------------------


def two_providers(web=None, **kwargs):
    local = FakeLLM([Event("answer", "from local")])
    hosted = FakeLLM([Event("answer", "from hosted")])
    hosted.key, hosted.name, hosted.hosted, hosted.base_url = "mistral", "Mistral", True, "https://mistral.test/v1"
    session = Session(CFG, local, make_fetch(web or {}), providers={"lmstudio": local, "mistral": hosted}, **kwargs)
    return session, local, hosted


def test_a_single_client_becomes_a_one_provider_registry():
    llm = FakeLLM()
    session = Session(CFG, llm, make_fetch({}))
    assert session.provider == "lmstudio" and session.llm is llm
    assert session.providers == {"lmstudio": llm}


async def test_switching_provider_changes_which_client_answers():
    session, local, hosted = two_providers({A: make_page(A)})
    await session.scrape(A)
    assert [e.text for e in await ask_all(session)] == ["from local"]
    await session.set_provider("mistral")
    assert session.provider == "mistral" and session.llm is hosted
    assert [e.text for e in await ask_all(session)] == ["from hosted"]
    assert len(local.calls) == 1 and len(hosted.calls) == 1


async def test_an_unknown_provider_is_rejected_and_nothing_changes():
    session, local, _ = two_providers()
    with pytest.raises(ValueError, match="Unknown provider"):
        await session.set_provider("openai")
    assert session.llm is local


async def test_switching_provider_is_refused_while_a_run_or_scrape_is_in_progress():
    session, local, _ = two_providers()
    session.busy = True
    with pytest.raises(SessionBusy):
        await session.set_provider("mistral")
    assert session.llm is local


async def test_the_default_model_factory_builds_for_the_active_provider():
    cfg = Config(request_delay=0.0, api_key="sk-test", base_url="http://lm.test:1234/v1", mistral_base_url="https://mistral.test/v1")
    local, hosted = FakeLLM(), FakeLLM()
    hosted.key = "mistral"
    session = Session(cfg, local, make_fetch({}), providers={"lmstudio": local, "mistral": hosted})
    assert session.model_factory("m").openai_api_base == "http://lm.test:1234/v1"
    await session.set_provider("mistral")
    model = session.model_factory("m")
    assert model.openai_api_base == "https://mistral.test/v1" and model.openai_api_key.get_secret_value() == "sk-test"


async def test_a_paused_run_follows_the_provider_it_started_on():
    model = ScriptedChat(script=[ai("", call("ask_user", "c1", question="Which plan?", options=["A", "B"]))])
    session, _, _ = two_providers(model_factory=lambda model_id: model)
    assert (await run_all(session, "pick a plan"))[-1].kind == "needs_input"
    assert session.resumable and session.pending["question"] == "Which plan?"
    await session.set_provider("mistral")
    assert session.foreign_run is session.run and not session.resumable and session.pending is None
    await session.set_provider("lmstudio")
    assert session.foreign_run is None and session.resumable and session.pending["question"] == "Which plan?"


async def test_agent_errors_name_the_active_providers_endpoint():
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://mistral.test/v1/chat/completions"))
    session, _, _ = two_providers(model_factory=lambda model_id: ScriptedChat(script=[error]))
    await session.set_provider("mistral")
    events = await run_all(session, "anything")
    assert "Cannot reach Mistral at https://mistral.test/v1" in events[-1].text


# ---- agent runs -------------------------------------------------------------

SITE_A = "https://site.test/a"
OTHER = "https://other.test/x"


def agent_session(web, script, *, cfg=CFG, store=None, saver=None, llm=None):
    model = ScriptedChat(script=list(script))
    session = Session(cfg, llm or FakeLLM(), make_fetch(web), store=store, checkpointer=saver, model_factory=lambda model_id: model)
    return session, model


async def run_all(session, instruction, **kwargs):
    return [event async for event in session.run_agent(instruction, "qwen-test", **kwargs)]


async def resume_all(session, value):
    return [event async for event in session.resume_run(value)]


async def proceed_all(session):
    return [event async for event in session.proceed_run()]


async def test_agent_errors_name_the_providers_server_not_lm_studio():
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"))
    llm = FakeLLM()
    llm.name = "Mistral"
    session, _ = agent_session({}, [error], llm=llm)
    events = await run_all(session, "anything")
    assert events[-1].kind == "error" and "Cannot reach Mistral" in events[-1].text


async def test_agent_fetches_a_page_answers_and_records_the_exchange():
    web = {SITE_A: make_page(SITE_A, markdown="# Pricing\n\nStarter costs ten dollars.")}
    session, _ = agent_session(web, [ai("", call("fetch_page", "c1", url=SITE_A)), ai("Starter is ten dollars.", reasoning="check")])
    events = await run_all(session, f"Look at {SITE_A} and tell me the Starter price")
    assert events[-1].kind == "done"
    assert [p.url for p in session.pages] == [SITE_A]
    user, assistant = session.history
    assert user.content.startswith("Look at")
    assert (assistant.content.strip(), assistant.reasoning, assistant.sources) == ("Starter is ten dollars.", "check", (SITE_A,))
    assert [(t.name, t.ok) for t in assistant.trace] == [("fetch_page", True)]
    assert session.last_context.sources == (SITE_A,) and session.run.status == "done"


async def test_an_unlisted_site_pauses_for_approval_and_deny_resumes_with_a_refusal():
    web = {SITE_A: make_page(SITE_A), OTHER: make_page(OTHER)}
    session, _ = agent_session(web, [ai("", call("fetch_page", "c1", url=OTHER)), ai("could not")])
    events = await run_all(session, f"Look at {SITE_A}")
    assert events[-1].kind == "needs_input" and events[-1].payload["host"] == "other.test"
    assert session.run.status == "paused" and session.pending["kind"] == "site" and session.resumable
    resumed = await resume_all(session, {"allow": False})
    assert next(e for e in resumed if e.kind == "tool_result").ok is False and resumed[-1].kind == "done"
    assert session.pages == [] and session.run.status == "done" and session.pending is None


async def test_approving_a_site_fetches_it():
    web = {SITE_A: make_page(SITE_A), OTHER: make_page(OTHER)}
    session, _ = agent_session(web, [ai("", call("fetch_page", "c1", url=OTHER)), ai("got it")])
    await run_all(session, f"Look at {SITE_A}")
    resumed = await resume_all(session, {"allow": True})
    assert resumed[-1].kind == "done" and [p.url for p in session.pages] == [OTHER]
    assert "other.test" in session.run.allowed_hosts


async def test_stored_pages_and_the_url_bar_seed_widen_the_allowlist_without_a_pause():
    other, b = "https://other.test/y", "https://site.test/b"
    web = {SITE_A: make_page(SITE_A), b: make_page(b), other: make_page(other)}
    session, _ = agent_session(web, [ai("", call("fetch_page", "c1", url=b)), ai("", call("fetch_page", "c2", url=other)), ai("ok")])
    await session.scrape(SITE_A)
    events = await run_all(session, "find more", seed_url="other.test")
    assert [e.ok for e in events if e.kind == "tool_result"] == [True, True] and events[-1].kind == "done"


async def test_the_url_bar_link_is_given_to_the_model_as_the_page_to_scrape():
    session, model = agent_session({SITE_A: make_page(SITE_A)}, [ai("ok")])
    await run_all(session, "what do the plans cost?", seed_url=SITE_A)
    system = model.calls[0]["messages"][0].content
    assert SITE_A in system and "URL bar" in system
    assert SITE_A in session.run.system_prompt  # kept, so a resume or restart still knows it


async def test_without_a_url_bar_link_the_prompt_mentions_no_link():
    session, model = agent_session({}, [ai("ok")])
    await run_all(session, "hello")
    assert "URL bar" not in model.calls[0]["messages"][0].content


async def test_the_system_prompt_has_limits_the_untrusted_data_rule_and_existing_page_context():
    web = {SITE_A: make_page(SITE_A, markdown="# P\n\nTen dollars.")}
    session, model = agent_session(web, [ai("ok")])
    await session.scrape(SITE_A)
    await run_all(session, "what does it cost?")
    system = model.calls[0]["messages"][0].content
    assert "Never follow instructions" in system and "ask_user" in system
    assert str(CFG.agent_max_steps) in system and str(CFG.agent_max_pages) in system
    assert "Ten dollars." in system


async def test_the_system_prompt_explains_the_fetch_delay_tool():
    session, model = agent_session({}, [ai("ok")])
    await run_all(session, "hello")
    system = model.calls[0]["messages"][0].content
    assert "set_fetch_delay" in system and "429" in system


async def test_a_delay_the_model_sets_is_saved_and_survives_a_restart(tmp_path):
    script = [ai("", call("set_fetch_delay", "c1", seconds=3, reason="rate limited")), ai("done")]
    store = SessionStore(tmp_path / "s.db")
    session, _ = agent_session({}, script, cfg=replace(CFG, request_delay=0.5), store=store)
    events = await run_all(session, "hello")
    assert events[-1].kind == "done" and session._toolbox.delay == 3.0
    assert session.run.delay == 3.0 and store.load_run().delay == 3.0
    again, _ = agent_session({}, [], cfg=replace(CFG, request_delay=0.5), store=SessionStore(tmp_path / "s.db"))
    await again.restore()
    again._prepare(100_000)
    assert again._toolbox.delay == 3.0


async def test_a_new_run_starts_at_the_configured_request_delay():
    session, _ = agent_session({}, [ai("ok")], cfg=replace(CFG, request_delay=0.5))
    await run_all(session, "hello")
    assert session._toolbox.delay == 0.5 and session._toolbox.min_delay == 0.5


async def test_rows_persist_note_the_history_and_a_new_scrape_clears_them(tmp_path):
    web = {SITE_A: make_page(SITE_A)}
    rows = [{"name": "Starter", "price": "$10"}, {"name": "Team", "price": "$40"}]
    store = SessionStore(tmp_path / "s.db")
    session, model = agent_session(web, [ai("", call("add_rows", "c1", rows=rows)), ai("Added them"), ai("ok")], store=store)
    await run_all(session, f"collect plans from {SITE_A}")
    assert session.rows == rows and store.load().rows == rows
    assert session.history[-1].note == "2 rows added to the results table"
    assert len(store.load().history) == 2
    await run_all(session, "anything else?")
    messages = model.calls[-1]["messages"]
    assert [m.type for m in messages] == ["system", "human", "ai", "human"]  # no tool traces replayed
    assert "[2 rows added to the results table]" in messages[2].content
    await session.scrape(SITE_A)
    assert session.rows == [] and store.load().rows == [] and store.load().run is None
    assert [p.url for p in store.load().pages] == [SITE_A]


async def test_stop_leaves_a_resumable_run_and_proceed_finishes_without_duplicate_work():
    # Review Focus 4
    web = {SITE_A: make_page(SITE_A)}
    model = ScriptedChat(script=[ai("", call("fetch_page", "c1", url=SITE_A)), ai("finished")])

    def fetch_and_cancel(url, mode="fast", *, timeout=20.0):
        session.cancel()
        return web[url]

    session = Session(CFG, FakeLLM(), fetch_and_cancel, model_factory=lambda model_id: model)
    events = await run_all(session, f"read {SITE_A}")
    assert events[-1].kind == "limit" and events[-1].text == "stopped"
    assert session.run.status == "stopped" and session.resumable and session.history == []
    assert [p.url for p in session.pages] == [SITE_A]
    rest = await proceed_all(session)
    assert rest[-1].kind == "done" and session._toolbox.fetched == 1 and len(session.history) == 2


async def test_a_paused_run_is_restored_and_resumed_after_a_restart(tmp_path):
    # Review Focus 3
    web = {SITE_A: make_page(SITE_A, markdown="# P\n\nStarter ten dollars.")}
    first_script = [ai("", call("fetch_page", "c1", url=SITE_A)), ai("", call("ask_user", "c2", question="Which plan?", options=["Starter", "Team"]))]
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        session, _ = agent_session(web, first_script, store=SessionStore(tmp_path / "session.db"), saver=saver)
        events = await run_all(session, f"price at {SITE_A}")
        assert events[-1].kind == "needs_input"
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        store = SessionStore(tmp_path / "session.db")
        session, _ = agent_session(web, [ai("Team costs forty")], store=store, saver=saver)
        await session.restore()
        assert [p.url for p in session.pages] == [SITE_A]
        assert session.pending["question"] == "Which plan?" and session.resumable
        events = await resume_all(session, "Team")
        assert events[-1].kind == "done" and len(session.history) == 2
        assert store.load().run.status == "done"


async def test_a_run_records_which_provider_it_started_on():
    session, _ = agent_session({}, [ai("hello")])
    await run_all(session, "say hello")
    assert session.run.provider == "LM Studio"


async def test_a_run_from_another_provider_is_kept_but_cannot_be_resumed(tmp_path):
    # Tool-call ids and message shapes differ between providers, so a thread cannot cross over
    web = {SITE_A: make_page(SITE_A)}
    script = [ai("", call("ask_user", "c1", question="Which plan?", options=["Starter", "Team"]))]
    hosted = FakeLLM()
    hosted.name = "Mistral"
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        session, _ = agent_session(web, script, store=SessionStore(tmp_path / "session.db"), saver=saver, llm=hosted)
        assert (await run_all(session, "pick a plan"))[-1].kind == "needs_input"
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        store = SessionStore(tmp_path / "session.db")
        local, _ = agent_session(web, [ai("never")], store=store, saver=saver)
        await local.restore()
        assert local.foreign_run is local.run and local.foreign_run.provider == "Mistral"
        assert not local.resumable and local.pending is None
        with pytest.raises(RuntimeError, match="Mistral"):
            await resume_all(local, "Team")
        with pytest.raises(RuntimeError, match="Mistral"):
            await proceed_all(local)
        assert store.load_run().status == "paused"  # untouched, so switching back still works
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        back, _ = agent_session(web, [ai("Team it is")], store=SessionStore(tmp_path / "session.db"), saver=saver, llm=hosted)
        await back.restore()
        assert back.foreign_run is None and back.resumable and back.pending["question"] == "Which plan?"


async def test_a_run_cut_off_mid_stream_is_found_stopped_after_a_restart_and_proceeds(tmp_path):
    # Review Focus 3: the app died with the run record still saying "running"
    web = {SITE_A: make_page(SITE_A)}
    script = [ai("", call("fetch_page", "c1", url=SITE_A)), ai("finished")]
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        store = SessionStore(tmp_path / "session.db")
        session, _ = agent_session(web, script, store=store, saver=saver)
        stream = session.run_agent(f"read {SITE_A}", "qwen-test")
        async for event in stream:
            if event.kind == "tool_result":
                break
        await stream.aclose()
        assert store.load_run().status == "running"
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        session, _ = agent_session(web, [ai("finished")], store=SessionStore(tmp_path / "session.db"), saver=saver)
        await session.restore()
        assert session.run.status == "stopped" and session.resumable
        events = await proceed_all(session)
        assert events[-1].kind == "done" and len(session.history) == 2


async def test_a_resumable_run_whose_checkpoints_are_gone_is_ended_on_restore(tmp_path):
    # Review Focus 8: session.db survived but checkpoints.db did not
    script = [ai("", call("ask_user", "c1", question="Which plan?"))]
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        session, _ = agent_session({}, script, store=SessionStore(tmp_path / "session.db"), saver=saver)
        await run_all(session, "do it")
        assert session.pending is not None
    store = SessionStore(tmp_path / "session.db")
    session, _ = agent_session({}, [], store=store)  # a fresh, empty in-memory checkpointer
    await session.restore()
    assert session.run.status == "ended" and not session.resumable and session.pending is None
    assert store.load_run().status == "ended"


async def test_a_model_connection_error_leaves_the_run_resumable():
    error = openai.APIConnectionError(request=httpx.Request("POST", "http://lm.test/v1/chat/completions"))
    session, _ = agent_session({}, [error, ai("fine now")])
    events = await run_all(session, "do it")
    assert events[-1].kind == "error" and session.run.status == "error" and session.resumable
    assert session.history == []
    events = await proceed_all(session)
    assert events[-1].kind == "done" and session.run.status == "done"


async def test_a_context_limit_ends_the_run_without_recording_history():
    session, _ = agent_session({}, [ai("never")], cfg=replace(CFG, context_fraction=0.001, reply_reserve=0))
    events = await run_all(session, "x" * 8000)
    assert events[-1].kind == "limit" and events[-1].text == "context"
    assert session.history == [] and session.run.status == "ended" and not session.resumable


async def test_discarding_a_paused_run_ends_it_and_forgets_its_thread():
    session, _ = agent_session({}, [ai("", call("ask_user", "c1", question="Which?"))])
    await run_all(session, "do it")
    thread = session._thread_id()
    await session.discard_run()
    assert session.run.status == "ended" and session.pending is None and not session.resumable
    assert await session.checkpointer.aget_tuple({"configurable": {"thread_id": thread}}) is None


async def test_a_new_session_deletes_run_threads_and_wipes_the_store(tmp_path):
    web = {SITE_A: make_page(SITE_A)}
    store = SessionStore(tmp_path / "s.db")
    session, _ = agent_session(web, [ai("", call("ask_user", "c1", question="Which?"))], store=store)
    await session.scrape(SITE_A)
    await run_all(session, "do it")
    old_id, thread = store.session_id(), session._thread_id()
    await session.new_session()
    assert session.pages == [] and session.run is None and session.history == []
    loaded = store.load()
    assert (loaded.pages, loaded.rows, loaded.history, loaded.run) == ([], [], [], None) and store.session_id() != old_id
    assert await session.checkpointer.aget_tuple({"configurable": {"thread_id": thread}}) is None


async def test_agent_prompt_plus_reply_reserve_fits_the_window_even_with_history():
    filler = "lorem ipsum dolor sit amet " * 110
    md = "".join(f"# Section {n}\n\n{filler}\n\n" for n in range(10))
    web = {SITE_A: make_page(SITE_A, markdown=md)}
    session, model = agent_session(web, [], llm=FakeLLM(window=8192))
    await session.scrape(SITE_A)
    for n in range(7):
        model.script.append(ai("x" * 1980))
        await run_all(session, f"lorem q{n}")
    prompt_tokens = sum(estimate_tokens(m.content) for m in model.calls[-1]["messages"])
    assert prompt_tokens + CFG.reply_reserve <= 8192


async def test_two_pauses_in_one_step_are_answered_in_turn_through_the_session():
    # Review: parallel tool calls pause twice at once; each answer must reach its own question
    a, b = "https://a.test/x", "https://b.test/y"
    web = {SITE_A: make_page(SITE_A), a: make_page(a), b: make_page(b)}
    script = [ai("", call("fetch_page", "c1", url=a), call("fetch_page", "c2", url=b)), ai("done")]
    session, _ = agent_session(web, script)
    events = await run_all(session, f"Look at {SITE_A}")
    asked = []
    while session.pending is not None:
        asked.append(session.pending["host"])
        events = await resume_all(session, {"allow": len(asked) == 1})
    assert events[-1].kind == "done" and sorted(asked) == ["a.test", "b.test"]
    assert [host_of(page.url) for page in session.pages] == [asked[0]]


async def test_an_empty_final_answer_ends_the_run_and_offers_no_resume():
    # Review Focus 8: never offer a Resume that cannot work
    session, _ = agent_session({}, [ai("")])
    events = await run_all(session, "do it")
    assert events[-1].kind == "error"
    assert session.run.status == "ended" and not session.resumable


async def test_a_second_run_while_one_is_active_is_refused_and_the_guard_clears_afterwards():
    # two browser tabs share one Session
    web = {SITE_A: make_page(SITE_A)}
    session, model = agent_session(web, [ai("", call("fetch_page", "c1", url=SITE_A)), ai("finished")])
    stream = session.run_agent(f"read {SITE_A}", "qwen-test")
    await anext(stream)
    with pytest.raises(SessionBusy):
        await agent_events_list(session, "another instruction")
    with pytest.raises(SessionBusy):
        await session.scrape(SITE_A)
    with pytest.raises(SessionBusy):
        await session.new_session()
    await stream.aclose()
    model.script[:] = [ai("fine")]
    events = await run_all(session, "now it works")
    assert events[-1].kind == "done"


async def agent_events_list(session, instruction):
    return [event async for event in session.run_agent(instruction, "qwen-test")]


async def test_system_prompt_and_messages_together_fit_the_window():
    filler = "lorem ipsum dolor sit amet " * 110
    big = "".join(f"# S{k}\n\n{filler}\n\n" for k in range(6))
    urls = [f"https://site.test/p{n}" for n in range(3)]
    web = {SITE_A: make_page(SITE_A, markdown=big), **{u: make_page(u, markdown=big) for u in urls}}
    script = [ai("", call("fetch_page", f"c{n}", url=u)) for n, u in enumerate(urls)] + [ai("done")]
    session, model = agent_session(web, script, llm=FakeLLM(window=8192))
    await session.scrape(SITE_A)
    await run_all(session, "lorem")
    assert model.calls
    for model_call in model.calls:
        total = sum(estimate_tokens(m.content if isinstance(m.content, str) else "") for m in model_call["messages"])
        assert total + CFG.reply_reserve <= 8192


async def test_a_granted_page_allowance_survives_a_restart(tmp_path):
    urls = [f"https://site.test/p{n}" for n in range(2)]
    web = {u: make_page(u) for u in urls}
    script = [
        ai("", call("fetch_page", "c1", url=urls[0])),
        ai("", call("fetch_page", "c2", url=urls[1])),  # over the budget of 1: pauses
        ai("", call("fetch_page", "c3", url=urls[1])),  # after Continue it works
        ai("done"),
    ]
    store = SessionStore(tmp_path / "s.db")
    session, _ = agent_session(web, script, cfg=replace(CFG, agent_max_pages=1), store=store)
    events = await run_all(session, f"Look at {urls[0]} and {urls[1]}")
    assert events[-1].kind == "needs_input" and session.pending["reason"] == "pages"
    events = await resume_all(session, {"action": "continue"})
    assert events[-1].kind == "done" and session.run.max_pages == 2
    assert store.load_run().max_pages == 2
    again, _ = agent_session(web, [], cfg=replace(CFG, agent_max_pages=1), store=SessionStore(tmp_path / "s.db"))
    await again.restore()
    again._prepare(100_000)
    assert again._toolbox.max_pages == 2
    again._toolbox.grant_pages()
    assert again._toolbox.max_pages == 3  # the original allowance of 1 is still what a grant adds


async def test_the_chosen_model_is_remembered_per_provider_and_survives_a_new_session(tmp_path):
    local, hosted = FakeLLM(), FakeLLM()
    hosted.key, hosted.name = "mistral", "Mistral"
    store = SessionStore(tmp_path / "s.db")
    session = Session(CFG, local, make_fetch({}), store=store, providers={"lmstudio": local, "mistral": hosted})
    assert session.preferred_model is None
    session.remember_model("qwen-a")
    await session.set_provider("mistral")
    assert session.preferred_model is None
    session.remember_model("mistral-small")
    await session.new_session()

    again = Session(CFG, local, make_fetch({}), store=SessionStore(tmp_path / "s.db"), providers={"lmstudio": local, "mistral": hosted})
    assert again.preferred_model == "qwen-a"
    assert store.get_pref("provider") == "mistral"
    await again.set_provider("mistral")
    assert again.preferred_model == "mistral-small"


def test_remembering_a_model_without_a_store_is_a_no_op():
    session = Session(CFG, FakeLLM(), make_fetch({}))
    session.remember_model("qwen-a")
    assert session.preferred_model is None


def test_settings_are_remembered_across_restarts_and_default_when_unset(tmp_path):
    session = Session(CFG, FakeLLM(), make_fetch({}), store=SessionStore(tmp_path / "s.db"))
    assert session.setting("url", "") == ""
    session.set_setting("url", "https://site.test/a")
    again = Session(CFG, FakeLLM(), make_fetch({}), store=SessionStore(tmp_path / "s.db"))
    assert again.setting("url", "") == "https://site.test/a"


def test_settings_without_a_store_fall_back_to_the_default():
    session = Session(CFG, FakeLLM(), make_fetch({}))
    session.set_setting("url", "x")
    assert session.setting("url", "dflt") == "dflt"
