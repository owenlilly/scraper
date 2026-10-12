"""fetch_page tries the other fetch modes itself when one fails, so the model does not have to."""

import pytest

from fakes import make_page
from scraper.config import Config
from scraper.fetch import FetchError
from scraper.session import agent_system_prompt
from scraper.tools import ToolBox

URL = "https://site.test/pricing"
GOOD = make_page(URL, markdown="# Pricing\n\nStarter ten dollars.")
SHELL = make_page(URL, markdown="", looks_empty=True)


def blocked(retry_mode="stealthy"):
    return FetchError(f"HTTP 403 from {URL}", hint="The site may be blocking simple requests. Try Stealthy mode.", retry_mode=retry_mode)


def box_for(by_mode, **kw):
    """A ToolBox whose fetches follow `by_mode` (a page to return, or an error to raise, for each mode) and note which modes were tried."""
    tried: list[str] = []

    def fetch_fn(url, mode, *, timeout):
        tried.append(mode)
        outcome = by_mode[mode]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    pages: list = []
    return ToolBox(pages=pages, rows=[], fetch_fn=fetch_fn, allowed_hosts={"site.test"}, **kw), pages, tried


def test_a_blocked_page_is_tried_in_the_mode_the_site_error_suggests():
    box, pages, tried = box_for({"fast": blocked(), "dynamic": GOOD, "stealthy": GOOD})
    result = box.execute("fetch_page", {"url": URL})
    assert not result.is_error and "ten dollars" in result.text and tried == ["fast", "stealthy"]  # not dynamic: the error said stealthy
    assert "fast" in result.text and "HTTP 403" in result.text and "mode stealthy" in result.text and pages == [GOOD]


def test_a_nearly_empty_page_is_tried_in_the_next_mode_up():
    box, _, tried = box_for({"fast": SHELL, "dynamic": GOOD, "stealthy": GOOD})
    result = box.execute("fetch_page", {"url": URL})
    assert "ten dollars" in result.text and tried == ["fast", "dynamic"] and "nearly empty" in result.text


def test_it_goes_on_through_every_mode_until_one_works():
    box, _, tried = box_for({"fast": SHELL, "dynamic": blocked(), "stealthy": GOOD})
    assert "ten dollars" in box.execute("fetch_page", {"url": URL}).text and tried == ["fast", "dynamic", "stealthy"]


def test_an_empty_page_is_still_returned_when_the_better_mode_fails():
    box, pages, tried = box_for({"fast": SHELL, "dynamic": FetchError("The browser for this mode is not installed", hint="Run scrapling install."), "stealthy": GOOD})
    result = box.execute("fetch_page", {"url": URL})
    assert not result.is_error and pages == [SHELL] and tried == ["fast", "dynamic"]  # an empty page is more use than an error


def test_when_every_mode_fails_the_error_says_what_was_tried():
    box, _, tried = box_for({"fast": blocked(), "dynamic": blocked(), "stealthy": blocked(None)})
    result = box.execute("fetch_page", {"url": URL})
    assert result.is_error and "HTTP 403" in result.text and "fast" in result.text and "stealthy" in result.text and tried == ["fast", "stealthy"]


@pytest.mark.parametrize("message", ["HTTP 404 from https://site.test/pricing", "Could not fetch https://site.test/pricing: Name or service not known"])
def test_a_failure_that_another_mode_would_not_fix_is_not_retried(message):
    box, _, tried = box_for({"fast": FetchError(message), "dynamic": GOOD, "stealthy": GOOD})
    assert box.execute("fetch_page", {"url": URL}).is_error and tried == ["fast"]


def test_only_the_modes_this_server_allows_are_used():
    box, _, tried = box_for({"fast": blocked(), "dynamic": GOOD, "stealthy": GOOD}, allowed_modes=("fast", "dynamic"))
    result = box.execute("fetch_page", {"url": URL})
    assert result.is_error and tried == ["fast"]  # stealthy was suggested, but it is not allowed here
    box, _, tried = box_for({"fast": SHELL, "dynamic": GOOD, "stealthy": GOOD}, allowed_modes=("fast",))
    assert box.execute("fetch_page", {"url": URL}).is_error is False and tried == ["fast"]


def test_it_never_goes_back_to_a_simpler_mode_and_does_not_climb_past_the_one_asked_for():
    box, _, tried = box_for({"fast": GOOD, "dynamic": blocked(), "stealthy": GOOD})
    box.execute("fetch_page", {"url": URL, "mode": "dynamic"})
    assert tried == ["dynamic", "stealthy"]
    box, _, tried = box_for({"fast": GOOD, "dynamic": GOOD, "stealthy": blocked(None)})
    assert box.execute("fetch_page", {"url": URL, "mode": "stealthy"}).is_error and tried == ["stealthy"]


def test_each_attempt_costs_a_page_of_the_budget():
    box, _, tried = box_for({"fast": blocked(), "dynamic": GOOD, "stealthy": GOOD}, max_pages=1)
    result = box.execute("fetch_page", {"url": URL})
    assert result.is_error and result.limit == "pages" and "HTTP 403" in result.text and tried == ["fast"]
    box, _, tried = box_for({"fast": blocked(), "dynamic": GOOD, "stealthy": GOOD}, max_pages=2)
    assert not box.execute("fetch_page", {"url": URL}).is_error and box.fetched == 2


def test_asking_again_for_the_same_page_costs_nothing_even_when_a_fallback_was_needed():
    box, _, tried = box_for({"fast": blocked(), "dynamic": GOOD, "stealthy": GOOD})
    box.execute("fetch_page", {"url": URL})
    again = box.execute("fetch_page", {"url": URL})
    assert not again.is_error and tried == ["fast", "stealthy"]


def test_it_can_be_switched_off():
    box, _, tried = box_for({"fast": blocked(), "dynamic": GOOD, "stealthy": GOOD}, fallback=False)
    result = box.execute("fetch_page", {"url": URL})
    assert result.is_error and "mode='stealthy'" in result.text and tried == ["fast"]
    box, _, tried = box_for({"fast": SHELL, "dynamic": GOOD, "stealthy": GOOD}, fallback=False)
    assert "dynamic" in box.execute("fetch_page", {"url": URL}).text and tried == ["fast"]


def test_the_setting_is_on_unless_switched_off_and_the_agent_is_told_what_to_expect():
    assert Config().fetch_fallback is True
    from scraper.config import load

    assert load({"SCRAPER_FETCH_FALLBACK": "false"}).fetch_fallback is False
    on = agent_system_prompt(Config(fetch_modes="fast,dynamic,stealthy"))
    off = agent_system_prompt(Config(fetch_modes="fast,dynamic,stealthy", fetch_fallback=False))
    assert "automatically retries" in on and "automatically retries" not in off and "try another fetch mode" in off
