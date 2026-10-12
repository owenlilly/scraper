"""What a tool hands back, and how a tool reads its arguments."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False
    rows_added: int = 0
    fetched: tuple[str, ...] = ()
    limit: str | None = None


class ToolArgError(Exception):
    """The model called a tool with arguments that cannot be used. The message tells it what to fix."""


def str_arg(args: dict, key: str, *, required: bool = False) -> str | None:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolArgError(f"Missing required argument '{key}'.")
        return None
    if not isinstance(value, str):
        raise ToolArgError(f"Argument '{key}' must be a string.")
    return value


def int_arg(args: dict, key: str, default: int, low: int, high: int) -> int:
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgError(f"Argument '{key}' must be an integer.")
    return max(low, min(high, value))


def seconds_arg(args: dict, key: str) -> float:
    value = args.get(key)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            pass
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ToolArgError(f"Argument '{key}' must be a number of seconds, zero or more.")
    return float(value)
