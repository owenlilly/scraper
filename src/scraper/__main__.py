"""Entry point: python -m scraper."""

import os

# Scraped pages must never leave this machine: force LangChain and LangSmith cloud tracing off
# before anything from LangChain is imported or configured.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from collections.abc import Mapping  # noqa: E402
from contextlib import AsyncExitStack  # noqa: E402
from pathlib import Path  # noqa: E402

from nicegui import app, ui  # noqa: E402

from .agent import open_checkpointer  # noqa: E402
from .app import build_ui  # noqa: E402
from .config import ENV_PREFIX, Config, load, read_env  # noqa: E402
from .llm import LMStudio, MistralAPI  # noqa: E402
from .session import Session  # noqa: E402
from .store import SessionStore  # noqa: E402


def data_paths(cfg: Config) -> tuple[Path, Path]:
    data_dir = Path(cfg.data_dir).expanduser()
    return data_dir / "session.db", data_dir / "checkpoints.db"


def make_llms(cfg: Config) -> dict[str, LMStudio | MistralAPI]:
    """One client per provider the config makes available, in the order they are offered."""
    llms: dict[str, LMStudio | MistralAPI] = {"lmstudio": LMStudio(cfg.base_url, fallback_context=cfg.fallback_context)}
    if "mistral" in cfg.available_providers:
        llms["mistral"] = MistralAPI(cfg.mistral_base_url, cfg.api_key, fallback_context=cfg.fallback_context)
    return llms


def starting_provider(cfg: Config, llms: dict, store: SessionStore, env: Mapping[str, str]) -> str:
    """SCRAPER_PROVIDER when set, otherwise the provider used last time if it is still on offer."""
    if f"{ENV_PREFIX}PROVIDER" in env:
        return cfg.provider
    saved = store.get_pref("provider")
    return saved if saved in llms else cfg.provider


def main() -> None:
    env = read_env()
    cfg = load(env)
    session_db, checkpoints_db = data_paths(cfg)
    llms = make_llms(cfg)
    store = SessionStore(session_db)
    llm = llms[starting_provider(cfg, llms, store, env)]
    if llm.hosted:
        print(f"Starting on {llm.name} at {llm.base_url}: page content is sent to {llm.name}.", flush=True)
    session = Session(cfg, llm, store=store, providers=llms)
    stack = AsyncExitStack()

    async def startup() -> None:
        session.attach_checkpointer(await open_checkpointer(checkpoints_db, stack))
        await session.restore()

    app.on_startup(startup)
    app.on_shutdown(stack.aclose)

    @ui.page("/")
    def index() -> None:
        build_ui(cfg, session)

    ui.run(host=cfg.host, port=cfg.port, title="Page Chat", reload=False, show=True)


if __name__ in {"__main__", "__mp_main__"}:
    main()
