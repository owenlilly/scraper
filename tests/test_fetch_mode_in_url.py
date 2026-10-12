"""A model sometimes writes the fetch mode into the address (`?mode=fast`) instead of in the mode argument. The site never asked for that."""

from fakes import make_page
from scraper.tools import ToolBox

URL = "https://site.test/pricing"


def box_recording():
    asked: list[tuple[str, str]] = []

    def fetch_fn(url, mode, *, timeout):
        asked.append((url, mode))
        return make_page(url, markdown="# ok")

    return ToolBox(pages=[], rows=[], fetch_fn=fetch_fn, allowed_hosts={"site.test"}), asked


def test_a_mode_written_into_the_address_is_taken_out_and_used_as_the_mode():
    box, asked = box_recording()
    result = box.execute("fetch_page", {"url": URL + "?mode=dynamic"})
    assert asked == [(URL, "dynamic")] and "mode=dynamic" in result.text and "separate argument" in result.text


def test_the_rest_of_the_address_is_kept():
    box, asked = box_recording()
    box.execute("fetch_page", {"url": URL + "?page=2&mode=fast&sort=asc#top", "mode": "fast"})
    assert asked == [(URL + "?page=2&sort=asc#top", "fast")]


def test_a_mode_argument_that_was_really_given_wins_over_the_one_in_the_address():
    box, asked = box_recording()
    box.execute("fetch_page", {"url": URL + "?mode=fast", "mode": "stealthy"})
    assert asked == [(URL, "stealthy")]


def test_a_mode_parameter_that_is_not_a_fetch_mode_belongs_to_the_site_and_stays():
    box, asked = box_recording()
    box.execute("fetch_page", {"url": URL + "?mode=dark"})
    assert asked == [(URL + "?mode=dark", "fast")]


def test_an_address_without_one_is_untouched_and_says_nothing():
    box, asked = box_recording()
    result = box.execute("fetch_page", {"url": URL + "?page=2"})
    assert asked == [(URL + "?page=2", "fast")] and "separate argument" not in result.text


def test_the_tool_description_says_not_to_put_the_mode_in_the_address():
    from scraper.tools import build_tools

    box, _ = box_recording()
    description = next(t.description for t in build_tools(box) if t.name == "fetch_page")
    assert "not in the address" in description.lower() or "never in the address" in description.lower()
