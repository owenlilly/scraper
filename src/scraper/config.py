"""Defaults and environment overrides (SCRAPER_<FIELD>)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields

ENV_PREFIX = "SCRAPER_"


@dataclass(frozen=True)
class Config:
    base_url: str = "http://localhost:1234/v1"
    host: str = "127.0.0.1"
    port: int = 8080
    fallback_context: int = 16384
    context_fraction: float = 0.7
    reply_reserve: int = 2048
    history_reserve: int = 3000
    crawl_max_pages: int = 20
    crawl_depth: int = 1
    request_delay: float = 0.5
    fetch_timeout: float = 20.0
    agent_max_steps: int = 25
    agent_max_pages: int = 30
    tool_result_tokens: int = 3000
    data_dir: str = "~/.page-chat"


def load(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    defaults = Config()
    overrides = {}
    for field in fields(Config):
        key = ENV_PREFIX + field.name.upper()
        raw = env.get(key)
        if raw is None:
            continue
        kind = type(getattr(defaults, field.name))
        try:
            overrides[field.name] = kind(raw)
        except ValueError:
            raise ValueError(f"{key} must be {kind.__name__}, got {raw!r}") from None
    return Config(**overrides)
