"""Postgres persistence for one user's session. Same methods as scraper.store.SessionStore, every query scoped to user_id."""

import uuid
from dataclasses import asdict
from datetime import date

from psycopg.types.json import Json, Jsonb
from psycopg_pool import ConnectionPool

from scraper.config import Config
from scraper.crawl import normalize
from scraper.fetch import Page
from scraper.models import Message, TraceEntry
from scraper.store import LoadedSession, RunRecord
from scraper.usage import FIELDS, UsageLimitReached


class PgSessionStore:
    recovered = False  # SQLite can find a corrupt file; a database server does not

    def __init__(self, pool: ConnectionPool, user_id: str, cfg: Config) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        self.pool = pool
        self.user_id = user_id
        self.cfg = cfg
        self.closed = False

    def close(self) -> None:
        """The user is being deleted. A run that is still stopping may try to save once more; those writes are dropped,
        so nothing can bring the deleted data back. Reads still work."""
        self.closed = True

    # ---- session ids and run numbers ------------------------------------------

    def session_id(self) -> str:
        """Unique per user and session, and it starts with the user id, so checkpoint thread ids can never collide."""
        fresh = f"{self.user_id}:{uuid.uuid4().hex[:12]}"
        if self.closed:
            return fresh  # nothing is written for a user who is being deleted
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO app.sessions (user_id, session_id) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (self.user_id, fresh))
            return conn.execute("SELECT session_id FROM app.sessions WHERE user_id = %s", (self.user_id,)).fetchone()[0]

    def run_counter(self) -> int:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT run_counter FROM app.sessions WHERE user_id = %s", (self.user_id,)).fetchone()
            return row[0] if row else 0

    def next_run_number(self) -> int:
        if self.closed:
            return 0
        self.session_id()  # make sure the row exists
        with self.pool.connection() as conn:
            return conn.execute(
                "UPDATE app.sessions SET run_counter = run_counter + 1 WHERE user_id = %s RETURNING run_counter", (self.user_id,)
            ).fetchone()[0]

    # ---- preferences (kept across new sessions) ---------------------------------

    def get_pref(self, key: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT value FROM app.prefs WHERE user_id = %s AND key = %s", (self.user_id, key)).fetchone()
            return row[0] if row else None

    def set_pref(self, key: str, value: str) -> None:
        if self.closed:
            return
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.prefs (user_id, key, value) VALUES (%s, %s, %s) ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value",
                (self.user_id, key, value),
            )

    # ---- content ----------------------------------------------------------------

    def save_page(self, page: Page) -> None:
        if self.closed:
            return
        key = normalize(page.url)
        limit = self.cfg.max_page_chars
        with self.pool.connection() as conn:
            existing = conn.execute("SELECT position FROM app.pages WHERE user_id = %s AND key = %s", (self.user_id, key)).fetchone()
            if existing:
                position = existing[0]
            else:
                stored, top = conn.execute(
                    "SELECT count(*), COALESCE(max(position), 0) FROM app.pages WHERE user_id = %s", (self.user_id,)
                ).fetchone()
                if stored >= self.cfg.max_stored_pages:
                    raise UsageLimitReached(
                        f"You have {stored} pages stored, the most allowed. Start a new session to clear them, then carry on."
                    )
                position = top + 1
            conn.execute(
                """INSERT INTO app.pages (user_id, key, position, url, title, markdown, links, html, looks_empty)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, key) DO UPDATE SET url = EXCLUDED.url, title = EXCLUDED.title, markdown = EXCLUDED.markdown,
                       links = EXCLUDED.links, html = EXCLUDED.html, looks_empty = EXCLUDED.looks_empty""",
                (self.user_id, key, position, page.url, page.title, page.markdown[:limit], Jsonb(list(page.links)), page.html[:limit], page.looks_empty),
            )

    def append_rows(self, rows: list[dict]) -> None:
        if not rows or self.closed:
            return
        with self.pool.connection() as conn:
            stored = conn.execute("SELECT count(*) FROM app.rows WHERE user_id = %s", (self.user_id,)).fetchone()[0]
            if stored + len(rows) > self.cfg.max_result_rows:
                raise UsageLimitReached(
                    f"The results table holds {stored} rows, and {self.cfg.max_result_rows} is the most allowed. "
                    "Download them, then start a new session."
                )
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO app.rows (user_id, data) VALUES (%s, %s)", [(self.user_id, Json(row)) for row in rows])

    def append_history(self, messages: list[Message]) -> None:
        if self.closed:
            return
        records = [
            (
                self.user_id, m.role, m.content, m.reasoning, Jsonb(list(m.sources)), m.interrupted, m.note,
                Jsonb([asdict(t) for t in m.trace]),
            )
            for m in messages
        ]
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO app.history (user_id, role, content, reasoning, sources, interrupted, note, trace) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                records,
            )

    def save_run(self, record: RunRecord) -> None:
        if self.closed:
            return
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.runs (user_id, data) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data",
                (self.user_id, Jsonb(asdict(record))),
            )

    def load_run(self) -> RunRecord | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT data FROM app.runs WHERE user_id = %s", (self.user_id,)).fetchone()
            return RunRecord(**row[0]) if row else None

    def load(self) -> LoadedSession:
        with self.pool.connection() as conn:
            pages = [
                Page(url=r[0], title=r[1], markdown=r[2], links=tuple(r[3]), looks_empty=r[5], html=r[4])
                for r in conn.execute(
                    "SELECT url, title, markdown, links, html, looks_empty FROM app.pages WHERE user_id = %s ORDER BY position", (self.user_id,)
                )
            ]
            rows = [r[0] for r in conn.execute("SELECT data FROM app.rows WHERE user_id = %s ORDER BY id", (self.user_id,))]
            history = [
                Message(
                    role=r[0], content=r[1], reasoning=r[2], sources=tuple(r[3]), interrupted=r[4], note=r[5],
                    trace=tuple(TraceEntry(**t) for t in r[6]),
                )
                for r in conn.execute(
                    "SELECT role, content, reasoning, sources, interrupted, note, trace FROM app.history WHERE user_id = %s ORDER BY id",
                    (self.user_id,),
                )
            ]
        return LoadedSession(pages, rows, history, self.load_run())

    def wipe(self) -> None:
        """New session: forget the session, pages, results, history and run. Preferences and usage stay."""
        if self.closed:
            return
        with self.pool.connection() as conn:
            for table in ("pages", "rows", "history", "runs", "sessions"):
                conn.execute(f"DELETE FROM app.{table} WHERE user_id = %s", (self.user_id,))  # noqa: S608 (fixed table names)


class PgUsageStore:
    """Daily counters for scraper.usage.UsageMeter, shared by every user."""

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def try_add(self, user_id: str, day: date, field: str, limit: int) -> bool:
        if field not in FIELDS:
            raise ValueError(f"unknown usage field {field!r}")
        if limit <= 0:
            return False
        with self.pool.connection() as conn:
            row = conn.execute(
                f"""INSERT INTO app.usage (user_id, day, {field}) VALUES (%s, %s, 1)
                    ON CONFLICT (user_id, day) DO UPDATE SET {field} = app.usage.{field} + 1 WHERE app.usage.{field} < %s
                    RETURNING 1""",  # noqa: S608 (field is checked against FIELDS)
                (user_id, day, limit),
            ).fetchone()
            return row is not None

    def get(self, user_id: str, day: date) -> dict[str, int]:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT model_calls, pages FROM app.usage WHERE user_id = %s AND day = %s", (user_id, day)).fetchone()
        return {"model_calls": row[0], "pages": row[1]} if row else {field: 0 for field in FIELDS}
