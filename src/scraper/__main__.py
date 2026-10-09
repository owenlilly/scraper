from nicegui import ui

from .app import build_ui
from .config import load
from .llm import LMStudio
from .session import Session


def main() -> None:
    cfg = load()
    llm = LMStudio(cfg.base_url, fallback_context=cfg.fallback_context)
    session = Session(cfg, llm)

    @ui.page("/")
    def index() -> None:
        build_ui(cfg, llm, session)

    ui.run(host=cfg.host, port=cfg.port, title="Page Chat", reload=False, show=True)


if __name__ in {"__main__", "__mp_main__"}:
    main()
