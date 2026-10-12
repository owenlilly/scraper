import scraper.__main__ as entry
from scraper.config import Config, load


def test_requests_are_logged_unless_switched_off():
    assert Config().access_log is True
    assert load({"SCRAPER_ACCESS_LOG": "false"}).access_log is False
    assert load({"SCRAPER_ACCESS_LOG": "true"}).access_log is True


def _serve(monkeypatch, cfg: Config) -> dict:
    seen: dict = {}
    monkeypatch.setattr(entry, "load", lambda: cfg)
    monkeypatch.setattr(entry, "create_app", lambda c: "app")
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    entry.main()
    return seen


def test_the_server_is_started_with_the_access_log_the_settings_ask_for(monkeypatch):
    assert _serve(monkeypatch, Config(hosted=True, provider="lmstudio"))["access_log"] is True
    assert _serve(monkeypatch, Config(hosted=True, provider="lmstudio", access_log=False))["access_log"] is False


def test_the_start_up_notice_names_every_provider_people_can_choose():
    cfg = Config(hosted=True, provider="mistral", providers="lmstudio", api_key="k")
    assert "mistral, lmstudio" in entry.startup_notice(cfg)
