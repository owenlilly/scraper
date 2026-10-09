"""SQLite persistence for the current session: pages, rows, history and the run record."""

import json
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .crawl import normalize
from .fetch import Page
from .models import Message, TraceEntry

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pages (
    key TEXT PRIMARY KEY, position INTEGER NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
    markdown TEXT NOT NULL, links TEXT NOT NULL, html TEXT NOT NULL, looks_empty INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rows (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, reasoning TEXT NOT NULL,
    sources TEXT NOT NULL, interrupted INTEGER NOT NULL, note TEXT NOT NULL, trace TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
RESUMABLE = ("running", "paused", "stopped", "error")


@dataclass
class RunRecord:
    number: int
    instruction: str
    model: str = ""
    provider: str = ""  # which model server the thread belongs to; "" for runs saved before this field
    system_prompt: str = ""
    seed_url: str = ""
    status: str = "running"
    pending: dict | None = None
    allowed_hosts: list[str] = field(default_factory=list)
    fetched: int = 0
    max_pages: int = 0
    delay: float = 0.0  # seconds between fetches the model chose; 0 until it changes it
    fetched_urls: list[str] = field(default_factory=list)
    context_sources: list[str] = field(default_factory=list)
    rows_added: int = 0
    trace: list[dict] = field(default_factory=list)

    @property
    def resumable(self) -> bool:
        return self.status in RESUMABLE


@dataclass
class LoadedSession:
    pages: list[Page]
    rows: list[dict]
    history: list[Message]
    run: RunRecord | None


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.recovered = False
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, mode=0o700)
        self._init()

    # ---- connection handling -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _run(self, fn):
        with closing(self._connect()) as conn:
            with conn:
                return fn(conn)

    def _init(self) -> None:
        try:
            self._run(lambda conn: (conn.executescript(SCHEMA), conn.execute("SELECT count(*) FROM pages").fetchone()))
        except sqlite3.DatabaseError:
            self._set_aside()
            self._run(lambda conn: conn.executescript(SCHEMA))

    def _set_aside(self) -> None:
        self.recovered = True
        stamp = int(time.time())
        self.path.rename(self.path.with_name(f"{self.path.name}.corrupt-{stamp}"))
        for suffix in ("-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)

    # ---- meta ----------------------------------------------------------------

    def _meta_get(self, conn, key: str) -> str | None:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def session_id(self) -> str:
        def work(conn):
            value = self._meta_get(conn, "session_id")
            if value is None:
                value = uuid.uuid4().hex[:8]
                conn.execute("INSERT INTO meta (key, value) VALUES ('session_id', ?)", (value,))
            return value

        return self._run(work)

    def run_counter(self) -> int:
        return self._run(lambda conn: int(self._meta_get(conn, "run_counter") or 0))

    def next_run_number(self) -> int:
        def work(conn):
            number = int(self._meta_get(conn, "run_counter") or 0) + 1
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('run_counter', ?)", (str(number),))
            return number

        return self._run(work)

    # ---- preferences (kept across new sessions) -------------------------------

    def get_pref(self, key: str) -> str | None:
        row = self._run(lambda conn: conn.execute("SELECT value FROM prefs WHERE key = ?", (key,)).fetchone())
        return row[0] if row else None

    def set_pref(self, key: str, value: str) -> None:
        self._run(lambda conn: conn.execute("INSERT OR REPLACE INTO prefs (key, value) VALUES (?, ?)", (key, value)))

    # ---- content -------------------------------------------------------------

    def save_page(self, page: Page) -> None:
        key = normalize(page.url)

        def work(conn):
            existing = conn.execute("SELECT position FROM pages WHERE key = ?", (key,)).fetchone()
            if existing:
                position = existing[0]
            else:
                position = (conn.execute("SELECT COALESCE(MAX(position), 0) FROM pages").fetchone()[0]) + 1
            conn.execute(
                "INSERT OR REPLACE INTO pages (key, position, url, title, markdown, links, html, looks_empty) VALUES (?,?,?,?,?,?,?,?)",
                (key, position, page.url, page.title, page.markdown, json.dumps(list(page.links)), page.html, int(page.looks_empty)),
            )

        self._run(work)

    def append_rows(self, rows: list[dict]) -> None:
        self._run(lambda conn: conn.executemany("INSERT INTO rows (data) VALUES (?)", [(json.dumps(r, ensure_ascii=False),) for r in rows]))

    def append_history(self, messages: list[Message]) -> None:
        records = [
            (
                m.role, m.content, m.reasoning, json.dumps(list(m.sources)), int(m.interrupted), m.note,
                json.dumps([asdict(t) for t in m.trace], ensure_ascii=False),
            )
            for m in messages
        ]
        self._run(
            lambda conn: conn.executemany(
                "INSERT INTO history (role, content, reasoning, sources, interrupted, note, trace) VALUES (?,?,?,?,?,?,?)", records
            )
        )

    def save_run(self, record: RunRecord) -> None:
        data = json.dumps(asdict(record), ensure_ascii=False)
        self._run(lambda conn: conn.execute("INSERT OR REPLACE INTO run (id, data) VALUES (1, ?)", (data,)))

    def load_run(self) -> RunRecord | None:
        row = self._run(lambda conn: conn.execute("SELECT data FROM run WHERE id = 1").fetchone())
        return RunRecord(**json.loads(row[0])) if row else None

    def load(self) -> LoadedSession:
        def work(conn):
            pages = [
                Page(url=r[0], title=r[1], markdown=r[2], links=tuple(json.loads(r[3])), looks_empty=bool(r[5]), html=r[4])
                for r in conn.execute("SELECT url, title, markdown, links, html, looks_empty FROM pages ORDER BY position")
            ]
            rows = [json.loads(r[0]) for r in conn.execute("SELECT data FROM rows ORDER BY id")]
            history = [
                Message(
                    role=r[0], content=r[1], reasoning=r[2], sources=tuple(json.loads(r[3])), interrupted=bool(r[4]), note=r[5],
                    trace=tuple(TraceEntry(**t) for t in json.loads(r[6])),
                )
                for r in conn.execute("SELECT role, content, reasoning, sources, interrupted, note, trace FROM history ORDER BY id")
            ]
            return pages, rows, history

        pages, rows, history = self._run(work)
        return LoadedSession(pages, rows, history, self.load_run())

    def wipe(self) -> None:
        def work(conn):
            for table in ("pages", "rows", "history", "run", "meta"):
                conn.execute(f"DELETE FROM {table}")

        self._run(work)
