import pytest

from scraper.browser import button_selector, click_selector, field_selector


@pytest.mark.parametrize("text", ["#go", ".load-more", "[data-test=next]", "button.primary", "a:nth-child(2)", "form .submit", "ul > li a", "input[name=q]", "*"])
def test_these_are_taken_as_css_selectors(text):
    assert click_selector(text) == text and button_selector(text) == text and field_selector(text) == text


@pytest.mark.parametrize("text", ["Search", "Load more", "Next page", "Show 10 more results", "Add to cart", "Sign in"])
def test_text_with_or_without_spaces_is_taken_as_the_text_on_a_button(text):
    assert f'has-text("{text}")' in click_selector(text)
    assert f'has-text("{text}")' in button_selector(text)


def test_a_click_can_target_a_link_or_a_tab_by_its_text_but_a_form_button_cannot_be_a_link():
    assert 'a:has-text("Next page")' in click_selector("Next page")
    assert "a:has-text" not in button_selector("Next page")


def test_a_plain_word_is_a_fields_name_or_id():
    assert field_selector("q") == '[name="q"], [id="q"]'
    assert field_selector('we"ird') == '[name="we\\"ird"], [id="we\\"ird"]'
