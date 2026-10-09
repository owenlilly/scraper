"""Entry point: python -m scraper."""

import os

# Scraped pages must never leave this machine: force LangChain and LangSmith cloud tracing off
# before anything from LangChain is imported or configured.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from contextlib import AsyncExitStack  # noqa: E402
from pathlib import Path  # noqa: E402

from nicegui import app, ui  # noqa: E402

from .agent import open_checkpointer  # noqa: E402
from .app import build_ui  # noqa: E402
from .config import Config, load  # noqa: E402
from .llm import LMStudio  # noqa: E402
from .session import Session  # noqa: E402
from .store import SessionStore  # noqa: E402


def data_paths(cfg: Config) -> tuple[Path, Path]:
    data_dir = Path(cfg.data_dir).expanduser()
    return data_dir / "session.db", data_dir / "checkpoints.db"


def main() -> None:
    cfg = load()
    session_db, checkpoints_db = data_paths(cfg)
    llm = LMStudio(cfg.base_url, fallback_context=cfg.fallback_context)
    session = Session(cfg, llm, store=SessionStore(session_db))
    stack = AsyncExitStack()

    async def startup() -> None:
        session.attach_checkpointer(await open_checkpointer(checkpoints_db, stack))
        await session.restore()

    app.on_startup(startup)
    app.on_shutdown(stack.aclose)

    @ui.page("/")
    def index() -> None:
        build_ui(cfg, llm, session)

    ui.run(host=cfg.host, port=cfg.port, title="Page Chat", reload=False, show=True)


if __name__ in {"__main__", "__mp_main__"}:
    main()
