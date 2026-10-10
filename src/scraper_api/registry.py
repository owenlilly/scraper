"""One live Session per signed-in user, a cap on simultaneous work, and a per-user request limit."""

import asyncio
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field

from psycopg_pool import ConnectionPool

from scraper import fetch as fetch_mod
from scraper.config import Config
from scraper.hosted import make_hosted_fetch
from scraper.netguard import check_public_url
from scraper.session import Session
from scraper.usage import UsageMeter, UsageStore

from .pgstore import PgSessionStore


class TooBusy(Exception):
    """The server is already doing as much at once as it is set up for."""


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Too many requests. Try again in {retry_after} seconds.")
        self.retry_after = retry_after


class RunSlots:
    """A cap on streams (runs, questions, scrapes) in flight across all users. The API is one process, so this protects it."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0

    @contextmanager
    def hold(self):
        if self.active >= self.limit:
            raise TooBusy("The server is busy with other runs. Try again in a minute.")
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


class RateLimiter:
    """At most `limit` calls per user in any 60 seconds."""

    def __init__(self, limit: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self._clock = clock
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()  # check() runs in FastAPI's thread pool

    def check(self, user_id: str) -> None:
        with self._lock:
            now = self._clock()
            calls = self._calls[user_id]
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= self.limit:
                raise RateLimited(max(1, int(60 - (now - calls[0])) + 1))
            calls.append(now)
            if len(self._calls) > 10_000:  # forget users who have gone quiet
                for user in [u for u, c in self._calls.items() if not c or now - c[-1] >= 60]:
                    del self._calls[user]


@dataclass
class Entry:
    user_id: str
    session: Session
    meter: UsageMeter
    last_used: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)  # taken by whoever is changing the session's settings


class SessionRegistry:
    def __init__(
        self,
        cfg: Config,
        *,
        pool: ConnectionPool,
        checkpointer,
        llm,
        usage_store: UsageStore,
        idle_seconds: float = 1800.0,
        clock: Callable[[], float] = time.monotonic,
        fetch_base: Callable[..., fetch_mod.Page] = fetch_mod.fetch,
        url_check: Callable[[str], None] = check_public_url,
        model_factory=None,
    ) -> None:
        self.cfg = cfg
        self.pool = pool
        self.checkpointer = checkpointer
        self.llm = llm
        self.usage_store = usage_store
        self.idle_seconds = idle_seconds
        self._clock = clock
        self._fetch_base = fetch_base
        self._url_check = url_check
        self._model_factory = model_factory
        self._entries: dict[str, Entry] = {}
        self._creating: dict[str, asyncio.Lock] = {}

    async def get(self, user_id: str) -> Entry:
        entry = self._entries.get(user_id)
        if entry is None:
            async with self._creating.setdefault(user_id, asyncio.Lock()):
                entry = self._entries.get(user_id)
                if entry is None:
                    entry = await self._create(user_id)
                    self._entries[user_id] = entry
            self._creating.pop(user_id, None)
        entry.last_used = self._clock()
        return entry

    async def _create(self, user_id: str) -> Entry:
        meter = UsageMeter(user_id, self.usage_store, self.cfg)
        session = Session(
            self.cfg,
            self.llm,
            make_hosted_fetch(self.cfg, meter, base=self._fetch_base, check=self._url_check),
            store=PgSessionStore(self.pool, user_id, self.cfg),
            checkpointer=self.checkpointer,
            providers={self.llm.key: self.llm},
            usage=meter,
            model_factory=self._model_factory,
        )
        await session.restore()
        return Entry(user_id, session, meter, self._clock())

    def evict_idle(self) -> int:
        """Drop sessions nobody has touched for a while. Their state is in Postgres, so the next request restores it."""
        now = self._clock()
        stale = [u for u, e in self._entries.items() if not e.session.busy and now - e.last_used > self.idle_seconds]
        for user_id in stale:
            del self._entries[user_id]
        return len(stale)

    def peek(self, user_id: str) -> Entry | None:
        """The live entry if there is one. Never creates a session."""
        return self._entries.get(user_id)

    def forget(self, user_id: str) -> None:
        self._entries.pop(user_id, None)

    def __len__(self) -> int:
        return len(self._entries)
