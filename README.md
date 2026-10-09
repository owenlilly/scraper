# Page Chat

Give a local Qwen model, served by LM Studio, plain-English instructions about what to scrape. It fetches pages with [Scrapling](https://github.com/D4Vinci/Scrapling), extracts what you asked for, and hands back an answer plus a table you can download. Runs entirely on your machine in a small web UI.

## What it does

- **Tell it what to scrape.** "Go to example.com, follow the pricing links, and collect every plan with its price." Say how, too: "use stealthy mode", "use the `.price` selector", "only the /products pages".
- **It asks when it is stuck.** The agent pauses with a question when your instruction is ambiguous, a login or CAPTCHA blocks a page, or a choice changes the outcome. It also pauses when it hits its step, page or repeat limit (continue, wrap up, or stop) and before it fetches a site you did not name (allow or deny).
- **Nothing is lost.** The session (pages, results, chat) and any paused, stopped or crashed run are saved to disk. Reopen the app and press Resume, or answer the question that was waiting.
- **Results you can use.** Collected rows appear in a table with CSV and JSON download.
- **Still just chat.** With the Agent switch off you get the original behaviour: scrape a page with the URL bar and ask questions about it, no tools. Use this for models without tool calling.

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.14
- [LM Studio](https://lmstudio.ai/) with its local server running (default `http://localhost:1234/v1`) and a chat model loaded that supports tool calling (Qwen 3.x does). Load it with a context size you are happy with: the app reads the loaded size from LM Studio.

## Run

```bash
uv sync
uv run python -m scraper
```

Open http://127.0.0.1:8080 (it opens automatically). The server only listens on 127.0.0.1.

Dynamic and Stealthy fetch modes need browsers, installed once with:

```bash
uv run scrapling install
```

## Instruction cookbook

| You want | Say something like |
|---|---|
| A table of items | "Scrape https://shop.example/products, open each product page, and collect name, price and url." |
| A specific selector | "On https://example.com/list use the selector `.row .title` and give me the first 20 titles." |
| A hard site | "Fetch https://example.com/pricing in stealthy mode and tell me the cheapest plan." |
| To be consulted | "Find the contact email for the support team. Ask me if there is more than one." |
| A follow-up | "Which of those costs less than 50 dollars?" (it reuses the pages it already fetched) |

The agent may fetch only sites you named in the instruction, the URL bar seed, or pages already scraped. Anything else asks you first.

## Privacy

Everything stays on your machine: the model runs in LM Studio, pages and results are stored in `~/.page-chat/` (user-only permissions), and LangSmith or any other cloud tracing is switched off at startup. Scraped pages are untrusted: the agent is told never to follow instructions found inside them, and its allowlist and step budgets limit what a hostile page could make it do. That reduces the risk of prompt injection but does not remove it.

## Configuration

Environment variables override the defaults in `src/scraper/config.py`:

| Variable | Default | Meaning |
|---|---|---|
| `SCRAPER_BASE_URL` | `http://localhost:1234/v1` | LM Studio OpenAI-compatible endpoint |
| `SCRAPER_HOST` / `SCRAPER_PORT` | `127.0.0.1` / `8080` | Where the UI is served |
| `SCRAPER_DATA_DIR` | `~/.page-chat` | Where the session and run files live |
| `SCRAPER_AGENT_MAX_STEPS` | `25` | Model steps per round before the agent pauses to ask |
| `SCRAPER_AGENT_MAX_PAGES` | `30` | Page fetches per round before it pauses to ask |
| `SCRAPER_TOOL_RESULT_TOKENS` | `3000` | Size cap for each tool result |
| `SCRAPER_FALLBACK_CONTEXT` | `16384` | Context size assumed when the model is not loaded |
| `SCRAPER_CONTEXT_FRACTION` / `SCRAPER_REPLY_RESERVE` / `SCRAPER_HISTORY_RESERVE` | `0.7` / `2048` / `3000` | How much of the context window each part of a prompt may use |
| `SCRAPER_CRAWL_MAX_PAGES` / `SCRAPER_CRAWL_DEPTH` / `SCRAPER_REQUEST_DELAY` | `20` / `1` / `0.5` | Limits for the manual Follow links crawl |
| `SCRAPER_FETCH_TIMEOUT` | `20` | Seconds per page |

The LM Studio status in the footer re-checks every 15 seconds.

## Tests

```bash
uv run pytest
```
