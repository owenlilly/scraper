import asyncio

from fakes import FakeLLM, make_fetch, make_page
from nicegui import ui
from nicegui.testing import User

from scraper.app import build_ui
from scraper.config import Config
from scraper.llm import Event
from scraper.session import Session

SITE = "https://site.test/"


def register_page(llm, session):
    cfg = Config(request_delay=0.0)

    @ui.page("/")
    def index():
        build_ui(cfg, llm, session)


async def test_scrape_then_ask_shows_sources_and_a_streamed_answer(user: User):
    web = {SITE: make_page(SITE, title="Site Title", markdown="# Pricing\n\nTen dollars per month.")}
    llm = FakeLLM([Event("reasoning", "checking the page"), Event("answer", "It costs ten dollars.")])
    session = Session(Config(request_delay=0.0), llm, make_fetch(web))
    register_page(llm, session)

    await user.open("/")
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
