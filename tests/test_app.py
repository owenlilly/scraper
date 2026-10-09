import asyncio

from contextlib import AsyncExitStack

from fakes import FakeLLM, ScriptedChat, ai, call, make_fetch, make_page
from nicegui import ui
from nicegui.testing import User

from scraper.app import build_ui, describe_call, shorten, table_cell
from scraper.config import Config
from scraper.llm import Event
from scraper.session import Session
from scraper.agent import open_checkpointer
from scraper.store import SessionStore

SITE = "https://site.test/"


def register_page(llm, session):
    cfg = Config(request_delay=0.0)

    @ui.page("/")
    def index():
        build_ui(cfg, llm, session)


def agent_off(user):
    for element in user.find(marker="agent").elements:
        element.set_value(False)


def agent_session(web, script, *, cfg=None):
    model = ScriptedChat(script=list(script))
    session = Session(cfg or Config(request_delay=0.0), FakeLLM(), make_fetch(web), model_factory=lambda model_id: model)
    return session, model


async def test_scrape_then_ask_shows_sources_and_a_streamed_answer(user: User):
    web = {SITE: make_page(SITE, title="Site Title", markdown="# Pricing\n\nTen dollars per month.")}
    llm = FakeLLM([Event("reasoning", "checking the page"), Event("answer", "It costs ten dollars.")])
    session = Session(Config(request_delay=0.0), llm, make_fetch(web))
    register_page(llm, session)

    await user.open("/")
    agent_off(user)
    await user.should_see("Page Chat")
    await user.should_see("LM Studio connected")

    user.find(marker="url").type(SITE)
    user.find(marker="scrape").click()
    await user.should_see("Site Title")

    user.find(marker="question").type("How much does it cost?")
    user.find(marker="send").click()
    await user.should_see("It costs ten dollars.")
    assert "Ten dollars per month." in llm.calls[0][0]["content"]


async def test_a_failed_scrape_shows_an_inline_notice(user: User):
    llm = FakeLLM()
    session = Session(Config(request_delay=0.0), llm, make_fetch({}))
    register_page(llm, session)

    await user.open("/")
    agent_off(user)
    user.find(marker="url").type(SITE)
    user.find(marker="scrape").click()
    await user.should_see("HTTP 404")


class ExplodingLLM(FakeLLM):
    attempts = 0

    async def stream_chat(self, model, messages):
        self.attempts += 1
        yield Event("reasoning", "starting")
        raise RuntimeError("socket fell over")


async def test_an_unexpected_stream_failure_is_shown_and_unlocks_the_composer(user: User):
    web = {SITE: make_page(SITE, title="Site Title")}
    llm = ExplodingLLM()
    session = Session(Config(request_delay=0.0), llm, make_fetch(web))
    register_page(llm, session)

    await user.open("/")
    agent_off(user)
    await user.should_see("LM Studio connected")
    user.find(marker="url").type(SITE)
    user.find(marker="scrape").click()
    await user.should_see("Site Title")

    user.find(marker="question").type("first")
    user.find(marker="send").click()
    await user.should_see("socket fell over")

    user.find(marker="question").type("again")
    user.find(marker="send").click()
    for _ in range(30):
        if llm.attempts == 2:
            break
        await asyncio.sleep(0.1)
    assert llm.attempts == 2


async def test_error_after_an_interrupted_answer_is_not_blamed_on_that_answer(user: User):
    web = {SITE: make_page(SITE, title="Site Title")}
    llm = FakeLLM([Event("answer", "Par"), Event("error", "died")])
    session = Session(Config(request_delay=0.0), llm, make_fetch(web))
    register_page(llm, session)

    await user.open("/")
    agent_off(user)
    await user.should_see("LM Studio connected")
    user.find(marker="url").type(SITE)
    user.find(marker="scrape").click()
    await user.should_see("Site Title")

    user.find(marker="question").type("first")
    user.find(marker="send").click()
    await user.should_see("The answer was cut off.")

    llm.events = [Event("error", "LM Studio went away")]
    user.find(marker="question").type("second")
    user.find(marker="send").click()
    await user.should_see("LM Studio went away")
    await user.should_not_see("The answer was cut off.")


# ---- agent UI ---------------------------------------------------------------

OTHER = "https://other.test/x"


async def send_instruction(user, text):
    user.find(marker="question").type(text)
    user.find(marker="send").click()


def test_describe_call_and_shorten():
    assert describe_call("fetch_page", {"url": "https://a.test/", "mode": "stealthy"}) == "Fetching https://a.test/ (stealthy)"
    assert describe_call("fetch_page", {"url": "https://a.test/", "mode": None}) == "Fetching https://a.test/"
    assert describe_call("extract", {"url": "u", "selector": ".price"}) == "Extracting .price from u"
    assert describe_call("add_rows", {"rows": [{}, {}]}) == "Adding 2 row(s)"
    assert describe_call("mystery", {}) == "mystery"
    assert shorten("x" * 2000).endswith("[shortened]") and shorten("short") == "short"


async def test_an_agent_run_shows_the_tool_log_and_the_final_answer(user: User):
    web = {SITE: make_page(SITE, title="Site Title", markdown="# Pricing\n\nTen dollars per month.")}
    session, _ = agent_session(web, [ai("", call("fetch_page", "c1", url=SITE)), ai("The Starter plan costs ten dollars.")])
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await user.should_not_see(marker="stop")
    await send_instruction(user, f"Look at {SITE} and tell me the price")
    await user.should_see(f"Fetching {SITE}")
    await user.should_see("ten dollars")
    await user.should_see("Site Title")  # the fetched page joins the page list
    assert session.run.status == "done" and len(session.history) == 2


async def test_ask_user_shows_a_question_card_and_an_option_button_resumes_the_run(user: User):
    script = [ai("", call("ask_user", "c1", question="Which plan?", options=["Starter", "Team"])), ai("Team costs forty dollars.")]
    session, _ = agent_session({SITE: make_page(SITE)}, script)
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Price at {SITE}")
    await user.should_see("Which plan?")
    user.find(marker="option:Team").click()
    await user.should_see("Team costs forty dollars.")
    assert session.run.status == "done"


async def test_a_free_text_answer_resumes_an_ask_question(user: User):
    script = [ai("", call("ask_user", "c1", question="Which plan?")), ai("Noted, the Pro plan.")]
    session, _ = agent_session({SITE: make_page(SITE)}, script)
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Price at {SITE}")
    await user.should_see("Which plan?")
    user.find(marker="pause-note").type("Pro")
    user.find(marker="answer").click()
    await user.should_see("Noted, the Pro plan.")


async def test_site_approval_card_allow_resumes_and_fetches(user: User):
    web = {SITE: make_page(SITE), OTHER: make_page(OTHER, title="Other Page")}
    session, _ = agent_session(web, [ai("", call("fetch_page", "c1", url=OTHER)), ai("Got the other page.")])
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Look at {SITE}")
    await user.should_see("May I fetch pages from other.test?")
    user.find(marker="allow").click()
    await user.should_see("Got the other page.")
    assert [p.url for p in session.pages] == [OTHER]


async def test_a_step_limit_card_offers_continue_wrap_up_and_stop(user: User):
    web = {SITE: make_page(SITE)}
    session, _ = agent_session(
        web, [ai("", call("fetch_page", "c1", url=SITE)), ai("Wrapped up answer.")], cfg=Config(request_delay=0.0, agent_max_steps=1)
    )
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Look at {SITE}")
    await user.should_see("I have used 1 of my 1 steps")
    user.find(marker="action:wrap_up").click()
    await user.should_see("Wrapped up answer.")


async def test_discarding_a_paused_run_unlocks_the_composer(user: User):
    script = [ai("", call("ask_user", "c1", question="Which plan?"))]
    session, _ = agent_session({SITE: make_page(SITE)}, script)
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Price at {SITE}")
    await user.should_see("Which plan?")
    user.find(marker="discard").click()
    await user.should_not_see("Which plan?")
    assert session.run.status == "ended" and session.pending is None


async def test_a_model_error_shows_a_notice_with_resume(user: User):
    import httpx
    import openai

    error = openai.APIConnectionError(request=httpx.Request("POST", "http://lm.test/v1/chat/completions"))
    session, _ = agent_session({SITE: make_page(SITE)}, [error, ai("Fine now.")])
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Look at {SITE}")
    await user.should_see("Cannot reach LM Studio")
    user.find(kind=ui.button, content="Resume").click()
    await user.should_see("Fine now.")


# ---- restore and new session ------------------------------------------------


async def leave_a_cut_off_run(tmp_path, web, script, instruction):
    """Run until the first tool result or pause, then drop everything unfinished, as a crash would."""
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        model = ScriptedChat(script=list(script))
        session = Session(
            Config(request_delay=0.0),
            FakeLLM(),
            make_fetch(web),
            store=SessionStore(tmp_path / "session.db"),
            checkpointer=saver,
            model_factory=lambda model_id: model,
        )
        stream = session.run_agent(instruction, "qwen-test")
        async for event in stream:
            if event.kind in ("tool_result", "needs_input"):
                break
        await stream.aclose()


async def restored_session(stack, tmp_path, web, script):
    saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
    model = ScriptedChat(script=list(script))
    session = Session(
        Config(request_delay=0.0),
        FakeLLM(),
        make_fetch(web),
        store=SessionStore(tmp_path / "session.db"),
        checkpointer=saver,
        model_factory=lambda model_id: model,
    )
    await session.restore()
    return session


async def test_a_cut_off_run_shows_a_resume_banner_after_a_restart_and_resume_finishes_it(user: User, tmp_path):
    # Review Focus 3
    web = {SITE: make_page(SITE, title="Site Title")}
    await leave_a_cut_off_run(tmp_path, web, [ai("", call("fetch_page", "c1", url=SITE)), ai("Finished the job.")], f"Look at {SITE}")
    async with AsyncExitStack() as stack:
        session = await restored_session(stack, tmp_path, web, [ai("Finished the job.")])
        register_page(FakeLLM(), session)
        await user.open("/")
        await user.should_see("A run was interrupted.")
        await user.should_see("Site Title")  # the pages were restored too
        user.find(marker="resume").click()
        await user.should_see("Finished the job.")
        assert session.run.status == "done"


async def test_a_paused_run_shows_its_question_on_launch_and_an_option_resumes_it(user: User, tmp_path):
    # Review Focus 3
    web = {SITE: make_page(SITE)}
    script = [ai("", call("ask_user", "c1", question="Which plan?", options=["Starter", "Team"]))]
    await leave_a_cut_off_run(tmp_path, web, script, f"Price at {SITE}")
    async with AsyncExitStack() as stack:
        session = await restored_session(stack, tmp_path, web, [ai("Team costs forty dollars.")])
        register_page(FakeLLM(), session)
        await user.open("/")
        await user.should_see("Which plan?")
        await user.should_see(f"Price at {SITE}")  # the chat shows which task the question belongs to
        user.find(marker="option:Team").click()
        await user.should_see("Team costs forty dollars.")


async def test_the_resume_banner_can_discard_the_run(user: User, tmp_path):
    web = {SITE: make_page(SITE)}
    await leave_a_cut_off_run(tmp_path, web, [ai("", call("fetch_page", "c1", url=SITE)), ai("never")], f"Look at {SITE}")
    async with AsyncExitStack() as stack:
        session = await restored_session(stack, tmp_path, web, [])
        register_page(FakeLLM(), session)
        await user.open("/")
        await user.should_see("A run was interrupted.")
        user.find(marker="discard").click()
        await user.should_not_see("A run was interrupted.")
        assert session.run.status == "ended" and not session.resumable


async def test_a_corrupt_session_file_gives_a_fresh_session_and_a_notice(user: User, tmp_path):
    # Review Focus 8
    (tmp_path / "session.db").write_bytes(b"not a database" * 100)
    session = Session(Config(request_delay=0.0), FakeLLM(), make_fetch({}), store=SessionStore(tmp_path / "session.db"))
    await session.restore()
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("could not be read")
    await user.should_see("No pages yet.")


async def test_new_session_clears_pages_and_chat_after_confirmation(user: User):
    web = {SITE: make_page(SITE, title="Site Title")}
    session, _ = agent_session(web, [ai("The answer.")])
    await session.scrape(SITE)
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("Site Title")
    user.find(marker="new-session").click()
    await user.should_see(marker="confirm-new")
    user.find(marker="confirm-new").click()
    await user.should_see("No pages yet.")
    assert session.pages == [] and session.history == []


# ---- results table ----------------------------------------------------------


def test_table_cell():
    assert table_cell(None) == "" and table_cell("x") == "x" and table_cell(3) == 3
    assert table_cell({"a": [1]}) == '{"a": [1]}'


async def test_rows_appear_in_a_table_with_downloads(user: User):
    web = {SITE: make_page(SITE, title="Site Title")}
    rows = [{"name": "Starter", "price": "$10"}, {"name": "Team", "price": "$40"}]
    session, _ = agent_session(web, [ai("", call("add_rows", "c1", rows=rows)), ai("Added the plans.")])
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await user.should_not_see("Download CSV")
    await send_instruction(user, f"Collect plans from {SITE}")
    await user.should_see("Added the plans.")
    await user.should_see("Results: 2 row(s)")
    await user.should_see(ui.table)
    user.find(marker="download-csv").click()
    download = await user.download.next()
    assert "name,price" in download.text and "Starter" in download.text
    user.find(marker="download-json").click()
    download = await user.download.next()
    assert '"Team"' in download.text


async def test_a_new_manual_scrape_clears_the_results_table(user: User):
    web = {SITE: make_page(SITE, title="Site Title")}
    session, _ = agent_session(web, [ai("", call("add_rows", "c1", rows=[{"a": 1}])), ai("Added.")])
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Collect from {SITE}")
    await user.should_see("Results: 1 row(s)")
    user.find(marker="url").type(SITE)
    user.find(marker="scrape").click()
    await user.should_see(marker="confirm-scrape")  # results exist, so ask before replacing them
    assert session.rows == [{"a": 1}]
    user.find(marker="confirm-scrape").click()
    await user.should_not_see("Download CSV")
    assert session.rows == []


async def test_a_context_limit_shows_a_notice_that_explains_it(user: User):
    cfg = Config(request_delay=0.0, context_fraction=0.001, reply_reserve=0)
    session, _ = agent_session({SITE: make_page(SITE)}, [ai("never")], cfg=cfg)
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, "x" * 8000)
    await user.should_see("no longer fits the model's context window")


async def test_cancelling_the_scrape_confirmation_keeps_everything(user: User):
    web = {SITE: make_page(SITE, title="Site Title")}
    session, _ = agent_session(web, [ai("", call("add_rows", "c1", rows=[{"a": 1}])), ai("Added.")])
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Collect from {SITE}")
    await user.should_see("Results: 1 row(s)")
    user.find(marker="url").type(SITE)
    user.find(marker="scrape").click()
    await user.should_see(marker="cancel-scrape")
    user.find(marker="cancel-scrape").click()
    await user.should_see("Results: 1 row(s)")
    assert session.rows == [{"a": 1}]
