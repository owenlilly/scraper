# Page Chat

Scrape a web page with [Scrapling](https://github.com/D4Vinci/Scrapling) and ask a local Qwen model, served by LM Studio, questions about it. Runs entirely on your machine in a small web UI.

## What it does

- Enter a URL and choose a fetch mode: Fast (plain HTTP), Dynamic (a real browser, for JavaScript pages) or Stealthy (anti-bot).
- Optionally follow same-domain links (depth 1 to 3, at most 20 pages).
- Ask questions in a chat. Answers stream in, the model's reasoning sits in a collapsible block, and the pages it used are highlighted in the page list.
- The panel on the left shows exactly the text the model receives.

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.14
- [LM Studio](https://lmstudio.ai/) with its local server running (default `http://localhost:1234/v1`) and a chat model loaded. Load the model with a context size you are happy with: the app reads the loaded size from LM Studio.

## Run

```bash
uv sync
uv run python -m scraper
```

Open http://127.0.0.1:8080 (it opens automatically). The server only listens on 127.0.0.1.

Dynamic and Stealthy modes need browsers, installed once with:

```bash
uv run scrapling install
```

## Configuration

Environment variables override the defaults in `src/scraper/config.py`:

| Variable | Default | Meaning |
|---|---|---|
| `SCRAPER_BASE_URL` | `http://localhost:1234/v1` | LM Studio OpenAI-compatible endpoint |
| `SCRAPER_HOST` / `SCRAPER_PORT` | `127.0.0.1` / `8080` | Where the UI is served |
| `SCRAPER_FALLBACK_CONTEXT` | `16384` | Context size assumed when the model is not loaded |
| `SCRAPER_CRAWL_MAX_PAGES` | `20` | Page cap when following links (the first page counts) |
| `SCRAPER_CRAWL_DEPTH` | `1` | Default link depth |
| `SCRAPER_REQUEST_DELAY` | `0.5` | Seconds between crawl requests |
| `SCRAPER_FETCH_TIMEOUT` | `20` | Seconds per page |

## How context works

If all the scraped text fits in 70% of the model's context window, it is sent in full. Otherwise the pages are split into chunks by heading and the chunks most relevant to your question (BM25 keyword ranking) are sent instead. The line under the page list says which mode the last answer used.

## Caveat: prompt injection

Scraped pages are untrusted. The system prompt tells the model to treat page text as data and never follow instructions found in it, which reduces but does not remove the risk. Do not point a tool-using model at pages you do not trust.

## Tests

```bash
uv run pytest
```
