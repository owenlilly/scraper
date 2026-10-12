"""Debug mode: a local log of what the app did, for review. Off unless SCRAPER_DEBUG is true.

It records metadata about every fetch, browser action, search, tool call and run (addresses, modes, statuses, sizes, timings, errors) as
one JSON object per line in `<debug_dir>/debug-YYYY-MM-DD.jsonl`. A short preview of text (page text, search results, tool results) is kept
only up to SCRAPER_DEBUG_BODY_CHARS characters, and 0 keeps none. Keys, tokens, cookies and passwords are never written. The files can
still hold what people searched for and which pages they read, so they are readable by their owner only and debug mode is for the person
running the server, not a setting to leave on for other people's data without telling them."""

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger("scraper.debug")
MAX_TEXT = 1000
HIDDEN = "[hidden]"
SECRET_WORDS = ("authorization", "api_key", "api-key", "apikey", "cookie", "password", "secret", "token")

_scope: ContextVar[dict] = ContextVar("debug_scope", default={})


def clean_url(url: str) -> str:
    """The address without any user:password@ part. Query strings are kept: they are most of what a fetch is."""
    try:
        parts = urlsplit(url)
        if parts.username is None and parts.password is None:
            return url
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except ValueError:
        return url


def _cut(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else f"{text[:limit]}... [{len(text) - limit} more characters]"


def _plain(value, key: str = ""):
    if any(word in key.lower() for word in SECRET_WORDS):
        return HIDDEN
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _cut(clean_url(value) if value.startswith(("http://", "https://")) and " " not in value else value)
    if isinstance(value, dict):
        return {str(k): _plain(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in list(value)[:50]]
    return _cut(str(value))


class DebugLog:
    def __init__(self, enabled: bool = False, directory: str = "debug-logs", body_chars: int = 500) -> None:
        self.enabled = enabled
        self.directory = Path(directory).expanduser()
        self.body_chars = max(0, body_chars)
        self._lock = threading.Lock()
        self._warned = False

    def event(self, name: str, **fields) -> None:
        if not self.enabled:
            return
        entry = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": name, **_scope.get(), **fields}
        line = json.dumps({k: _plain(v, k) for k, v in entry.items()}, ensure_ascii=False)
        try:
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = self.directory / f"debug-{datetime.now(UTC):%Y-%m-%d}.jsonl"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError as exc:  # a log that cannot be written must never stop the app
            if not self._warned:
                self._warned = True
                log.warning("debug log cannot be written to %s: %s", self.directory, exc)


_current = DebugLog()


def configure(cfg, directory: str | None = None) -> DebugLog:
    global _current
    _current = DebugLog(cfg.debug, directory or cfg.debug_dir, cfg.debug_body_chars)
    return _current


def current() -> DebugLog:
    return _current


def event(name: str, **fields) -> None:
    _current.event(name, **fields)


def enabled() -> bool:
    return _current.enabled


def preview(text: str | None) -> str | None:
    """The start of some text, as long as the settings allow, or None when no text is to be kept."""
    if not _current.enabled or _current.body_chars <= 0 or not text:
        return None
    return _cut(text, _current.body_chars)


def stopwatch() -> Callable[[], int]:
    """A timer that starts now: calling it gives the milliseconds since."""
    started = time.perf_counter()
    return lambda: round((time.perf_counter() - started) * 1000)


@contextmanager
def scope(**fields):
    """Adds `fields` (who, which run, which tool) to every event written inside the block, in this thread or task."""
    token = _scope.set({**_scope.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _scope.reset(token)
