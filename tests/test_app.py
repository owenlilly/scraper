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
