import pytest

from scraper.config import Config, load


def test_defaults():
    cfg = load({})
    assert cfg == Config()
    assert cfg.base_url == "http://localhost:1234/v1"
    assert cfg.host == "127.0.0.1"
    assert cfg.crawl_max_pages == 20
    assert cfg.crawl_depth == 1
    assert cfg.request_delay == 0.5
    assert cfg.fallback_context == 16384
    assert cfg.context_fraction == 0.7


def test_env_overrides_are_converted_to_the_field_type():
    cfg = load(
        {
            "SCRAPER_PORT": "9000",
            "SCRAPER_REQUEST_DELAY": "0.1",
            "SCRAPER_BASE_URL": "http://other:1234/v1",
        }
    )
    assert cfg.port == 9000
    assert cfg.request_delay == 0.1
    assert cfg.base_url == "http://other:1234/v1"


def test_bad_value_names_the_variable():
    with pytest.raises(ValueError, match="SCRAPER_PORT"):
        load({"SCRAPER_PORT": "not-a-number"})
