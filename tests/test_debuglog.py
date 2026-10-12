import json
import os
import stat

from scraper import debuglog
from scraper.config import Config, load


def lines(directory):
    files = sorted(directory.glob("debug-*.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text().splitlines()]


def on(tmp_path, **kw):
    return debuglog.configure(Config(debug=True, debug_dir=str(tmp_path), **kw))


def test_nothing_is_written_and_no_folder_is_made_unless_debug_is_on(tmp_path):
    log = debuglog.configure(Config(debug=False, debug_dir=str(tmp_path / "logs")))
    log.event("fetch.request", url="https://a.test/")
    assert not (tmp_path / "logs").exists() and log.enabled is False


def test_an_event_is_one_json_line_with_a_time_and_a_name(tmp_path):
    on(tmp_path)
    debuglog.event("fetch.request", url="https://a.test/x", mode="fast")
    [entry] = lines(tmp_path)
    assert entry["event"] == "fetch.request" and entry["url"] == "https://a.test/x" and entry["mode"] == "fast"
    assert entry["ts"].endswith("Z") or "+00:00" in entry["ts"]


def test_the_file_is_named_for_the_day_and_only_the_owner_can_read_it(tmp_path):
    on(tmp_path)
    debuglog.event("x")
    [path] = list(tmp_path.glob("debug-*.jsonl"))
    assert len(path.stem.split("-")) == 4  # debug-YYYY-MM-DD
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(tmp_path).st_mode) == 0o700


def test_a_scope_adds_who_and_what_to_everything_inside_it_and_then_goes_away(tmp_path):
    on(tmp_path)
    with debuglog.scope(user="alice", run=3):
        with debuglog.scope(tool="fetch_page"):
            debuglog.event("fetch.request", url="https://a.test/")
        debuglog.event("inside")
    debuglog.event("outside")
    first, second, third = lines(tmp_path)
    assert (first["user"], first["run"], first["tool"]) == ("alice", 3, "fetch_page")
    assert (second["user"], "tool" in second) == ("alice", False) and "user" not in third


def test_long_text_is_cut_and_says_how_much_was_cut(tmp_path):
    on(tmp_path)
    debuglog.event("x", text="a" * 5000)
    assert len(lines(tmp_path)[0]["text"]) < 1100 and "more characters" in lines(tmp_path)[0]["text"]


def test_a_body_preview_is_as_long_as_the_setting_allows_and_absent_at_zero(tmp_path):
    on(tmp_path, debug_body_chars=20)
    debuglog.event("fetch.response", preview=debuglog.preview("p" * 100))
    assert lines(tmp_path)[0]["preview"].startswith("p" * 20) and "p" * 21 not in lines(tmp_path)[0]["preview"].replace("more", "")
    other = tmp_path / "none"
    debuglog.configure(Config(debug=True, debug_dir=str(other), debug_body_chars=0))
    assert debuglog.preview("secret page text") is None


def test_secrets_are_never_written(tmp_path):
    on(tmp_path)
    debuglog.event("x", headers={"Authorization": "Bearer abc", "x-api-key": "k1", "Cookie": "s=1", "content-type": "text/html"}, api_key="k2", nested={"password": "p"})
    entry = lines(tmp_path)[0]
    assert entry["headers"] == {"Authorization": "[hidden]", "x-api-key": "[hidden]", "Cookie": "[hidden]", "content-type": "text/html"}
    assert entry["api_key"] == "[hidden]" and entry["nested"]["password"] == "[hidden]"
    assert "abc" not in json.dumps(entry) and "k1" not in json.dumps(entry)


def test_credentials_in_an_address_are_removed(tmp_path):
    on(tmp_path)
    debuglog.event("x", url="https://user:pass@a.test/p?q=1")
    assert lines(tmp_path)[0]["url"] == "https://a.test/p?q=1"
    assert debuglog.clean_url("not a url") == "not a url"


def test_a_failure_to_write_never_breaks_the_program(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("in the way")
    debuglog.configure(Config(debug=True, debug_dir=str(blocker / "sub")))  # a folder cannot be made here
    debuglog.event("x")  # must not raise


def test_values_that_are_not_json_are_written_as_text(tmp_path):
    on(tmp_path)
    debuglog.event("x", error=ValueError("boom"), path=tmp_path, items=(1, 2), nothing=None)
    entry = lines(tmp_path)[0]
    assert "boom" in entry["error"] and entry["items"] == [1, 2] and entry["nothing"] is None


def test_the_settings_default_to_off_and_a_local_folder():
    cfg = Config()
    assert cfg.debug is False and cfg.debug_dir == "debug-logs" and cfg.debug_body_chars == 500
    loaded = load({"SCRAPER_DEBUG": "true", "SCRAPER_DEBUG_DIR": "/var/log/pc", "SCRAPER_DEBUG_BODY_CHARS": "0"})
    assert (loaded.debug, loaded.debug_dir, loaded.debug_body_chars) == (True, "/var/log/pc", 0)
