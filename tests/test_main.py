import importlib
import os

from scraper import __main__ as entry
from scraper.config import Config
from scraper.llm import LMStudio, MistralAPI
from scraper.store import SessionStore


def test_importing_the_entry_point_forces_cloud_tracing_off(monkeypatch):
    # Scraped pages must never reach a tracing service
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    importlib.reload(entry)
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_data_paths_expand_the_home_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    session_db, checkpoints_db = entry.data_paths(Config(data_dir="~/.page-chat"))
    assert session_db == tmp_path / ".page-chat" / "session.db"
    assert checkpoints_db == tmp_path / ".page-chat" / "checkpoints.db"


def test_without_a_key_only_lm_studio_is_built():
    llms = entry.make_llms(Config())
    assert list(llms) == ["lmstudio"]
    assert isinstance(llms["lmstudio"], LMStudio) and llms["lmstudio"].hosted is False


def test_a_key_adds_the_hosted_mistral_client_with_its_own_endpoint():
    llms = entry.make_llms(Config(api_key="sk-test", mistral_base_url="https://mistral.test/v1"))
    assert list(llms) == ["lmstudio", "mistral"]
    assert isinstance(llms["mistral"], MistralAPI) and llms["mistral"].hosted is True
    assert llms["mistral"].base_url == "https://mistral.test/v1"
    assert llms["lmstudio"].base_url == "http://localhost:1234/v1"


def test_the_last_used_provider_is_the_starting_one(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    llms = entry.make_llms(Config(api_key="sk-test"))
    assert entry.starting_provider(Config(api_key="sk-test"), llms, store, {}) == "lmstudio"
    store.set_pref("provider", "mistral")
    assert entry.starting_provider(Config(api_key="sk-test"), llms, store, {}) == "mistral"


def test_an_explicit_provider_setting_beats_the_remembered_one(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    store.set_pref("provider", "mistral")
    cfg = Config(provider="lmstudio", api_key="sk-test")
    assert entry.starting_provider(cfg, entry.make_llms(cfg), store, {"SCRAPER_PROVIDER": "lmstudio"}) == "lmstudio"


def test_a_remembered_provider_that_is_no_longer_available_is_ignored(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    store.set_pref("provider", "mistral")
    cfg = Config()
    assert entry.starting_provider(cfg, entry.make_llms(cfg), store, {}) == "lmstudio"
