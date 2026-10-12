"""The NiceGUI interface is gone for good: the Next.js app in web/ is the only interface."""

import importlib.util
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_project_no_longer_depends_on_nicegui():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert not [d for d in project["project"]["dependencies"] if "nicegui" in d.lower()]
    assert "main_file" not in project["tool"]["pytest"]["ini_options"]  # a setting only NiceGUI's test plugin reads


def test_no_source_file_mentions_nicegui():
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "src").rglob("*.py") if "nicegui" in p.read_text().lower()]
    assert offenders == []


def test_the_old_interface_modules_are_gone():
    for name in ("scraper.app", "scraper.theme"):
        assert importlib.util.find_spec(name) is None, f"{name} should have been removed"


def test_no_pytest_plugin_for_nicegui_is_loaded():
    assert not (ROOT / "conftest.py").exists()
