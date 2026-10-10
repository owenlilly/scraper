"""Server-sent events: one frame per event, plus a final `end` frame so a finished stream is not mistaken for a dropped one."""

import json
from dataclasses import asdict, is_dataclass


def event_dict(event) -> dict:
    return asdict(event) if is_dataclass(event) else dict(event)


def frame(name: str, data: dict) -> bytes:
    # json.dumps never emits a raw newline, so one `data:` line always holds the whole object
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def error_frame(code: str, message: str) -> bytes:
    return frame("error", {"kind": "error", "code": code, "text": message})
