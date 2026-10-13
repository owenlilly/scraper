"""Plain data the repositories take and return. No database types leak out of a repository."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SourceConfig:
    id: int
    name: str
    enabled: bool = True
    schedule: str | None = None
    settings: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Run:
    id: int
    source_id: int
    status: str
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stats: dict = field(default_factory=dict)
    error: str | None = None
    kind: str = "live"
    retry_of: int | None = None
    requested_by: str | None = None
    heartbeat_at: datetime | None = None
    cancel_requested: bool = False
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FetchRef:
    id: int
    sha256: str
    size: int


@dataclass(frozen=True)
class FetchSummary:
    """One archived response as the spiders page lists it: no body, no headers."""

    id: int
    url: str
    final_url: str
    status: int
    fetched_at: datetime
    duration_ms: int | None
    size: int


@dataclass(frozen=True)
class RecordItem:
    """One validated, normalised item ready to store. `fetch_id` is the archived response it was parsed from."""

    natural_key: str
    data: dict
    content_hash: str
    fetch_id: int | None = None


@dataclass(frozen=True)
class RecordVersion:
    id: int
    record_id: int
    run_id: int
    fetch_id: int | None
    data: dict
    content_hash: str
    parser_version: str
    seen_at: datetime


@dataclass(frozen=True)
class UpsertResult:
    created: int = 0
    changed: int = 0
    unchanged: int = 0

    def __add__(self, other: "UpsertResult") -> "UpsertResult":
        return UpsertResult(self.created + other.created, self.changed + other.changed, self.unchanged + other.unchanged)
