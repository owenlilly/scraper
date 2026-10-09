import importlib
import os

from scraper import __main__ as entry
from scraper.config import Config


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
