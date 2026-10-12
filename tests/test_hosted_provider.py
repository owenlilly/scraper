import pytest

from scraper.config import Config, load
from scraper.llm import LMStudio, MistralAPI
from scraper.__main__ import startup_notice
from scraper.api.app import make_llms

HOSTED = {
    "SCRAPER_HOSTED": "true",
    "SCRAPER_DATABASE_URL": "postgresql://api@db/scraper",
    "SCRAPER_JWKS_URL": "https://web.test/api/auth/jwks",
    "SCRAPER_JWT_ISSUER": "https://web.test",
    "SCRAPER_JWT_AUDIENCE": "https://api.test",
    "SCRAPER_SERVICE_SECRET": "s3cret",
}


def test_hosted_mode_still_defaults_to_mistral_and_still_needs_its_key():
    with pytest.raises(ValueError, match="SCRAPER_HOSTED needs SCRAPER_API_KEY"):
        load(HOSTED)
    cfg = load({**HOSTED, "SCRAPER_API_KEY": "key"})
    assert (cfg.provider, cfg.available_providers) == ("mistral", ("mistral",))


def test_hosted_mode_can_run_on_a_model_server_you_run_yourself_and_then_needs_no_key():
    cfg = load({**HOSTED, "SCRAPER_PROVIDER": "lmstudio", "SCRAPER_BASE_URL": "http://127.0.0.1:1234/v1"})
    assert (cfg.hosted, cfg.provider, cfg.available_providers) == (True, "lmstudio", ("lmstudio",))
    assert cfg.base_url == "http://127.0.0.1:1234/v1" and cfg.api_key == ""


def test_the_other_hosted_settings_are_still_required_with_a_local_model_server():
    env = {k: v for k, v in HOSTED.items() if k != "SCRAPER_DATABASE_URL"}
    with pytest.raises(ValueError, match="SCRAPER_DATABASE_URL"):
        load({**env, "SCRAPER_PROVIDER": "lmstudio"})


def test_an_unknown_provider_is_still_refused_in_hosted_mode():
    with pytest.raises(ValueError, match="SCRAPER_PROVIDER must be one of"):
        load({**HOSTED, "SCRAPER_API_KEY": "key", "SCRAPER_PROVIDER": "gpt-nine"})


def _llm(cfg):
    return make_llms(cfg)[cfg.provider]


def test_make_llms_follows_the_configured_provider():
    mistral = _llm(Config(hosted=True, provider="mistral", api_key="k", mistral_base_url="https://mistral.test/v1"))
    assert isinstance(mistral, MistralAPI) and mistral.hosted is True and mistral.base_url == "https://mistral.test/v1"
    local = _llm(Config(hosted=True, provider="lmstudio", base_url="http://127.0.0.1:1234/v1"))
    assert isinstance(local, LMStudio) and local.hosted is False and local.base_url == "http://127.0.0.1:1234/v1"


def test_the_start_up_notice_says_where_page_text_goes():
    assert "sent to Mistral" in startup_notice(Config(hosted=True, provider="mistral", host="0.0.0.0", port=8080))
    local = startup_notice(Config(hosted=True, provider="lmstudio", base_url="http://127.0.0.1:1234/v1"))
    assert "http://127.0.0.1:1234/v1" in local and "Mistral" not in local
