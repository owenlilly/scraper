import os

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


def test_agent_defaults_and_overrides():
    cfg = load({})
    assert (cfg.agent_max_steps, cfg.agent_max_pages, cfg.tool_result_tokens) == (25, 30, 3000)
    assert cfg.data_dir == "~/.page-chat"
    assert load({"SCRAPER_AGENT_MAX_STEPS": "5"}).agent_max_steps == 5
    assert load({"SCRAPER_DATA_DIR": "/tmp/x"}).data_dir == "/tmp/x"


def test_provider_defaults_to_lm_studio_with_no_key():
    cfg = load({})
    assert cfg.provider == "lmstudio"
    assert cfg.api_key == ""


def test_mistral_has_its_own_endpoint_and_leaves_the_lm_studio_one_alone():
    cfg = load({"SCRAPER_PROVIDER": "mistral", "SCRAPER_API_KEY": "k"})
    assert cfg.provider == "mistral"
    assert cfg.mistral_base_url == "https://api.mistral.ai/v1"
    assert cfg.base_url == "http://localhost:1234/v1"


def test_the_two_endpoints_are_overridden_independently():
    cfg = load({"SCRAPER_BASE_URL": "http://lm.test/v1", "SCRAPER_MISTRAL_BASE_URL": "https://proxy.test/v1"})
    assert (cfg.base_url, cfg.mistral_base_url) == ("http://lm.test/v1", "https://proxy.test/v1")


def test_a_key_alone_makes_mistral_available_while_lm_studio_stays_the_start():
    cfg = load({"SCRAPER_API_KEY": "k"})
    assert cfg.provider == "lmstudio"
    assert cfg.available_providers == ("lmstudio", "mistral")


def test_without_a_key_only_lm_studio_is_available():
    assert load({}).available_providers == ("lmstudio",)


def test_api_key_prefers_scraper_api_key_then_mistral_api_key():
    assert load({"MISTRAL_API_KEY": "m"}).api_key == "m"
    assert load({"MISTRAL_API_KEY": "m", "SCRAPER_API_KEY": "s"}).api_key == "s"


def test_unknown_provider_names_the_variable_and_the_choices():
    with pytest.raises(ValueError, match="SCRAPER_PROVIDER.*lmstudio.*mistral"):
        load({"SCRAPER_PROVIDER": "openai"})


def test_mistral_provider_without_a_key_is_an_error():
    with pytest.raises(ValueError, match="SCRAPER_API_KEY"):
        load({"SCRAPER_PROVIDER": "mistral"})


def test_the_api_key_is_not_in_the_config_repr():
    assert "sekrit" not in repr(load({"SCRAPER_API_KEY": "sekrit"}))


# ---- .env -------------------------------------------------------------------


def test_load_reads_a_dotenv_file_from_the_working_directory(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('SCRAPER_PORT=9001\nSCRAPER_PROVIDER="mistral"\nSCRAPER_API_KEY=sk-from-file  # trailing comment\n')
    monkeypatch.chdir(tmp_path)
    for name in ("SCRAPER_PORT", "SCRAPER_PROVIDER", "SCRAPER_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    cfg = load()
    assert (cfg.port, cfg.provider, cfg.api_key) == (9001, "mistral", "sk-from-file")


def test_real_environment_variables_beat_the_dotenv_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SCRAPER_PORT=9001\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRAPER_PORT", "9002")
    assert load().port == 9002


def test_a_missing_dotenv_file_is_fine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCRAPER_PORT", raising=False)
    assert load().port == 8080


def test_dotenv_values_do_not_leak_into_the_process_environment(tmp_path, monkeypatch):
    # LangChain and LangSmith read os.environ; a stray line in .env must not change their behaviour
    (tmp_path / ".env").write_text("LANGSMITH_TRACING=true\nSCRAPER_PORT=9001\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    load()
    assert "LANGSMITH_TRACING" not in os.environ


def test_an_explicit_mapping_never_reads_the_dotenv_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SCRAPER_PORT=9001\n")
    monkeypatch.chdir(tmp_path)
    assert load({}).port == 8080


# ---- hosted mode --------------------------------------------------------------

HOSTED_ENV = {
    "SCRAPER_HOSTED": "true",
    "SCRAPER_DATABASE_URL": "postgresql://api@db/scraper",
    "SCRAPER_JWKS_URL": "https://web.test/api/auth/jwks",
    "SCRAPER_JWT_ISSUER": "https://web.test",
    "SCRAPER_JWT_AUDIENCE": "https://web.test",
    "SCRAPER_SERVICE_SECRET": "s3cret",
    "SCRAPER_API_KEY": "mistral-key",
}


def test_bool_env_values_are_parsed_not_truthy_strings():
    assert load({"SCRAPER_HOSTED": "false"}).hosted is False
    assert load({"SCRAPER_HOSTED": "0"}).hosted is False
    assert load({**HOSTED_ENV, "SCRAPER_HOSTED": "1"}).hosted is True
    with pytest.raises(ValueError, match="SCRAPER_HOSTED must be true or false"):
        load({"SCRAPER_HOSTED": "maybe"})


def test_hosted_mode_forces_mistral_and_only_the_fast_fetch_mode():
    cfg = load(HOSTED_ENV)
    assert cfg.hosted and cfg.provider == "mistral"
    assert cfg.available_providers == ("mistral",)
    assert cfg.allowed_modes == ("fast",)


def test_local_mode_allows_every_fetch_mode():
    assert load({}).allowed_modes == ("fast", "dynamic", "stealthy")


def test_fetch_modes_can_be_widened_and_are_validated():
    assert load({**HOSTED_ENV, "SCRAPER_FETCH_MODES": "fast, dynamic"}).allowed_modes == ("fast", "dynamic")
    with pytest.raises(ValueError, match="SCRAPER_FETCH_MODES"):
        load({**HOSTED_ENV, "SCRAPER_FETCH_MODES": "fast,teleport"})


@pytest.mark.parametrize(
    "missing",
    ["SCRAPER_DATABASE_URL", "SCRAPER_JWKS_URL", "SCRAPER_JWT_ISSUER", "SCRAPER_JWT_AUDIENCE", "SCRAPER_SERVICE_SECRET", "SCRAPER_API_KEY"],
)
def test_hosted_mode_refuses_to_start_without_its_secrets_and_endpoints(missing):
    env = {k: v for k, v in HOSTED_ENV.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        load(env)


def test_hosted_secrets_are_not_shown_in_repr():
    text = repr(load(HOSTED_ENV))
    for secret in ("s3cret", "mistral-key", "postgresql://api@db/scraper"):
        assert secret not in text


def test_cors_origins_are_split_and_trimmed():
    cfg = load({**HOSTED_ENV, "SCRAPER_CORS_ORIGINS": "https://a.test, https://b.test ,"})
    assert cfg.cors_origin_list == ("https://a.test", "https://b.test")
    assert load({}).cors_origin_list == ()


def test_hosted_limit_defaults_are_set():
    cfg = load(HOSTED_ENV)
    assert (cfg.daily_model_calls, cfg.daily_pages, cfg.max_concurrent_runs) == (300, 300, 5)
    assert (cfg.max_stored_pages, cfg.max_page_chars, cfg.max_result_rows, cfg.requests_per_minute) == (60, 200_000, 2000, 120)


def test_allowed_models_are_split_and_trimmed_and_default_to_any():
    assert load({}).allowed_model_list == ()
    cfg = load({**HOSTED_ENV, "SCRAPER_ALLOWED_MODELS": "mistral-small-latest, ministral-8b-latest,"})
    assert cfg.allowed_model_list == ("mistral-small-latest", "ministral-8b-latest")


def test_a_missing_api_key_in_hosted_mode_is_reported_as_a_hosted_problem():
    env = {k: v for k, v in HOSTED_ENV.items() if k != "SCRAPER_API_KEY"}
    with pytest.raises(ValueError) as caught:
        load(env)
    assert "SCRAPER_HOSTED needs SCRAPER_API_KEY" in str(caught.value) and "SCRAPER_PROVIDER" not in str(caught.value)
