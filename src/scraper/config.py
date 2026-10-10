"""Defaults and environment overrides (SCRAPER_<FIELD>), optionally from a .env file."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import dotenv_values

ENV_PREFIX = "SCRAPER_"
PROVIDERS = ("lmstudio", "mistral")
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
FETCH_MODES = ("fast", "dynamic", "stealthy")  # kept equal to fetch.MODES by a test
HOSTED_REQUIRED = {
    "database_url": "SCRAPER_DATABASE_URL",
    "jwks_url": "SCRAPER_JWKS_URL",
    "jwt_issuer": "SCRAPER_JWT_ISSUER",
    "jwt_audience": "SCRAPER_JWT_AUDIENCE",
    "service_secret": "SCRAPER_SERVICE_SECRET",
    "api_key": "SCRAPER_API_KEY",
}


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
    # Hosted, multi-user mode (the FastAPI service). Everything below is ignored when hosted is False.
    hosted: bool = False
    database_url: str = field(default="", repr=False)
    jwks_url: str = ""  # where the web app publishes its signing keys
    jwt_issuer: str = ""
    jwt_audience: str = ""
    service_secret: str = field(default="", repr=False)  # lets the web app call the account-deletion route
    cors_origins: str = ""  # comma-separated browser origins allowed to call the API
    cors_origin_regex: str = ""  # optional pattern, for preview deployments
    fetch_modes: str = ""  # comma-separated; empty means every mode locally and only "fast" when hosted
    allowed_models: str = ""  # comma-separated model ids users may pick when hosted; empty means any the provider lists
    daily_model_calls: int = 300  # per user per UTC day
    daily_pages: int = 300  # per user per UTC day
    max_stored_pages: int = 60  # per user
    max_page_chars: int = 200_000  # stored per page; longer text is cut
    max_result_rows: int = 2000  # per user
    max_concurrent_runs: int = 5  # across all users: the API is one process
    requests_per_minute: int = 120  # per user, on routes that do not stream

    @property
    def available_providers(self) -> tuple[str, ...]:
        """LM Studio is always offered locally; Mistral once a key is set. Hosted offers only Mistral."""
        if self.hosted:
            return ("mistral",)
        return ("lmstudio", "mistral") if self.api_key else ("lmstudio",)

    @property
    def allowed_modes(self) -> tuple[str, ...]:
        if self.fetch_modes.strip():
            return tuple(m.strip() for m in self.fetch_modes.split(",") if m.strip())
        return ("fast",) if self.hosted else FETCH_MODES

    @property
    def allowed_model_list(self) -> tuple[str, ...]:
        return tuple(m.strip() for m in self.allowed_models.split(",") if m.strip())

    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        return tuple(o.strip() for o in self.cors_origins.split(",") if o.strip())


def _parse(kind: type, raw: str, key: str):
    if kind is bool:  # bool("false") is True, so spell the values out
        lowered = raw.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off", ""):
            return False
        raise ValueError(f"{key} must be true or false, got {raw!r}")
    try:
        return kind(raw)
    except ValueError:
        raise ValueError(f"{key} must be {kind.__name__}, got {raw!r}") from None


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
        overrides[f.name] = _parse(type(getattr(defaults, f.name)), raw, key)
    if overrides.get("hosted"):
        overrides["provider"] = "mistral"  # LM Studio runs on one person's machine, not on the server
    provider = overrides.get("provider", defaults.provider)
    if provider not in PROVIDERS:
        raise ValueError(f"{ENV_PREFIX}PROVIDER must be one of {', '.join(PROVIDERS)}, got {provider!r}")
    if "api_key" not in overrides and env.get("MISTRAL_API_KEY"):
        overrides["api_key"] = env["MISTRAL_API_KEY"]
    if provider == "mistral" and not overrides.get("hosted"):  # hosted mode reports its own, clearer, missing-key error below
        if not overrides.get("api_key"):
            raise ValueError(f"{ENV_PREFIX}PROVIDER=mistral needs {ENV_PREFIX}API_KEY (or MISTRAL_API_KEY)")
    if overrides.get("hosted"):
        for name, variable in HOSTED_REQUIRED.items():
            if not overrides.get(name):
                raise ValueError(f"{ENV_PREFIX}HOSTED needs {variable}")
    cfg = Config(**overrides)
    unknown = [m for m in cfg.allowed_modes if m not in FETCH_MODES]
    if unknown:
        raise ValueError(f"{ENV_PREFIX}FETCH_MODES has unknown mode(s) {', '.join(unknown)}; use {', '.join(FETCH_MODES)}")
    return cfg
