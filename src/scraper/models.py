"""Plain data shared by the session, the store and the UI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TraceEntry:
    name: str
    args: dict
    text: str
    ok: bool


@dataclass
class Message:
    role: str
    content: str
    reasoning: str = ""
    sources: tuple[str, ...] = ()
    interrupted: bool = False
    note: str = ""
    trace: tuple[TraceEntry, ...] = ()
