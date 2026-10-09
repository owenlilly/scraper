"""Defaults and environment overrides (SCRAPER_<FIELD>), optionally from a .env file."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import dotenv_values

ENV_PREFIX = "SCRAPER_"
PROVIDERS = ("lmstudio", "mistral")
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"


@dataclass(frozen=True)
class Config:
    provider: str = "lmstudio"
    api_key: str = field(default="", repr=False)
    base_url: str = "http://localhost:1234/v1"  # LM Studio
    mistral_base_url: str = MISTRAL_BASE_URL
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

    @property
    def available_providers(self) -> tuple[str, ...]:
        """LM Studio is always offered; Mistral once a key is set."""
        return ("lmstudio", "mistral") if self.api_key else ("lmstudio",)


def read_env() -> dict[str, str]:
    """`.env` in the working directory, overridden by real environment variables.

    Read into a dict rather than os.environ so a stray line in the file cannot change how
    LangChain or LangSmith behave."""
    from_file = {k: v for k, v in dotenv_values(Path.cwd() / ".env").items() if v is not None}
    return {**from_file, **os.environ}


def load(env: Mapping[str, str] | None = None) -> Config:
    env = read_env() if env is None else env
    defaults = Config()
    overrides = {}
    for f in fields(Config):
        key = ENV_PREFIX + f.name.upper()
        raw = env.get(key)
        if raw is None:
            continue
        kind = type(getattr(defaults, f.name))
        try:
            overrides[f.name] = kind(raw)
        except ValueError:
            raise ValueError(f"{key} must be {kind.__name__}, got {raw!r}") from None
    provider = overrides.get("provider", defaults.provider)
    if provider not in PROVIDERS:
        raise ValueError(f"{ENV_PREFIX}PROVIDER must be one of {', '.join(PROVIDERS)}, got {provider!r}")
    if "api_key" not in overrides and env.get("MISTRAL_API_KEY"):
        overrides["api_key"] = env["MISTRAL_API_KEY"]
    if provider == "mistral":
        if not overrides.get("api_key"):
            raise ValueError(f"{ENV_PREFIX}PROVIDER=mistral needs {ENV_PREFIX}API_KEY (or MISTRAL_API_KEY)")
    return Config(**overrides)
