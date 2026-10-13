import pytest

from scraper.config import Config
from scraper.pipeline import __main__ as cli


def test_list_names_the_sources(capsys):
    assert cli.main(["list"]) == 0
    assert "books_toscrape" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["worker"], ["exec-run", "1"], ["run", "books_toscrape"]])
def test_commands_that_need_the_database_say_so(monkeypatch, capsys, argv):
    monkeypatch.setattr(cli, "load", lambda: Config())
    assert cli.main(argv) == 2
    assert "SCRAPER_DATABASE_URL" in capsys.readouterr().err


def test_a_worker_needs_a_positive_concurrency():
    with pytest.raises(SystemExit):
        cli.main(["worker", "--concurrency", "0"])
