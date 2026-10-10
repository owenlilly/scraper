"""Hosted mode: per-user daily allowances for model calls and fetched pages."""

import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from .config import Config


class UsageLimitReached(Exception):
    """A daily allowance is spent. The message is written for the person to read."""


class UsageStore(Protocol):
    def try_add(self, user_id: str, day: date, field: str, limit: int) -> bool:
        """Add one to the count unless it is already at `limit`. Atomic. Returns whether it was added."""

    def get(self, user_id: str, day: date) -> dict[str, int]:
        """The counts so far for that day, with every field present."""


FIELDS = ("model_calls", "pages")


class MemoryUsageStore:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, date, str], int] = defaultdict(int)
        self._lock = threading.Lock()

    def try_add(self, user_id: str, day: date, field: str, limit: int) -> bool:
        with self._lock:
            if self._counts[(user_id, day, field)] >= limit:
                return False
            self._counts[(user_id, day, field)] += 1
            return True

    def get(self, user_id: str, day: date) -> dict[str, int]:
        with self._lock:
            return {field: self._counts[(user_id, day, field)] for field in FIELDS}


def _utc_today() -> date:
    return datetime.now(UTC).date()


class UsageMeter:
    def __init__(self, user_id: str, store: UsageStore, cfg: Config, *, today: Callable[[], date] = _utc_today) -> None:
        self.user_id = user_id
        self.store = store
        self._limits = {"model_calls": cfg.daily_model_calls, "pages": cfg.daily_pages}
        self._today = today

    def _refused(self, what: str, limit: int) -> UsageLimitReached:
        return UsageLimitReached(
            f"Daily {what} limit reached ({limit} a day). It resets at 00:00 UTC. Your session and run are saved, so you can resume then."
        )

    def charge_model_call(self) -> None:
        if not self.store.try_add(self.user_id, self._today(), "model_calls", self._limits["model_calls"]):
            raise self._refused("model-call", self._limits["model_calls"])

    def charge_page(self) -> None:
        if not self.store.try_add(self.user_id, self._today(), "pages", self._limits["pages"]):
            raise self._refused("page", self._limits["pages"])

    def exhausted(self) -> str | None:
        """The message for the first spent allowance, or None while there is room in both."""
        used = self.store.get(self.user_id, self._today())
        if used["model_calls"] >= self._limits["model_calls"]:
            return str(self._refused("model-call", self._limits["model_calls"]))
        if used["pages"] >= self._limits["pages"]:
            return str(self._refused("page", self._limits["pages"]))
        return None

    def snapshot(self) -> dict:
        day = self._today()
        used = self.store.get(self.user_id, day)
        return {
            "day": day.isoformat(),
            "model_calls": used["model_calls"],
            "model_calls_limit": self._limits["model_calls"],
            "pages": used["pages"],
            "pages_limit": self._limits["pages"],
            "resets_at": datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC).isoformat(),
        }
