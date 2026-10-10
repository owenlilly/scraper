"""Entry point: python -m scraper_api"""

import os

# Scraped pages must never reach a tracing service: switch LangChain and LangSmith tracing off before anything from
# LangChain is imported or configured.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import logging  # noqa: E402

import uvicorn  # noqa: E402

from scraper.config import load  # noqa: E402

from .app import create_app  # noqa: E402


def startup_notice(cfg) -> str:
    where = f"{cfg.host}:{cfg.port}"
    if cfg.provider == "lmstudio":
        return f"Serving on {where}. Using the model server at {cfg.base_url}; page text goes only to that server."
    return f"Serving on {where}. Page text is sent to Mistral for every run."


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = load()
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from None
    if not cfg.hosted:
        raise SystemExit("scraper_api only runs in hosted mode. Set SCRAPER_HOSTED=true (see .env.example).")
    print(startup_notice(cfg), flush=True)
    # One process on purpose: each user's live Session lives in memory (see the design spec, "Process model").
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, workers=1, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
