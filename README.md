# Page Chat

Give a model plain-English instructions about what to scrape. By default it is a local model served by LM Studio (Qwen and Mistral models work); you can also use the hosted Mistral API, picked from a dropdown in the app. It fetches pages with [Scrapling](https://github.com/D4Vinci/Scrapling), extracts what you asked for, and hands back an answer plus a table you can download. Runs in a small web UI on your machine, and with the default local model nothing leaves it.

## What it does

- **Tell it what to scrape.** "Go to example.com, follow the pricing links, and collect every plan with its price." Say how, too: "use stealthy mode", "use the `.price` selector", "only the /products pages".
- **It asks when it is stuck.** The agent pauses with a question when your instruction is ambiguous, a login or CAPTCHA blocks a page, or a choice changes the outcome. It also pauses when it hits its step, page or repeat limit (continue, wrap up, or stop) and before it fetches a site you did not name (allow or deny).
- **Talk to it while it works.** Type a follow-up ("skip the blog pages", "also grab the SKU") and the agent reads it before its next step, keeping what it has gathered. Your message shows as waiting until it is read. Press Stop and anything unread goes back into the box.
- **Nothing is lost.** The session (pages, results, chat) and any paused, stopped or crashed run are saved to disk. Reopen the app and press Resume, or answer the question that was waiting.
- **Results you can use.** Collected rows appear in a table with CSV and JSON download.
- **Still just chat.** With the Agent switch off you get the original behaviour: scrape a page with the URL bar and ask questions about it, no tools. Use this for models without tool calling.

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.14
- [LM Studio](https://lmstudio.ai/) with its local server running (default `http://localhost:1234/v1`) and a chat model loaded that supports tool calling (Qwen 3.x does). Load it with a context size you are happy with: the app reads the loaded size from LM Studio. Mistral models (Ministral, Magistral, Devstral) work the same way; their reasoning shows up in the reasoning panel like Qwen's.
- Optional: a [Mistral API key](https://console.mistral.ai/) to add the hosted API as a second provider (see [Using Mistral's hosted API](#using-mistrals-hosted-api)).

## Run

```bash
uv sync
uv run python -m scraper
```

Open http://127.0.0.1:8080 (it opens automatically). The server only listens on 127.0.0.1.

The app picks up where you left off: the scraped pages, results, chat and any paused run come back, along with the URL bar, fetch mode, Follow links, depth and the Agent switch, and the model you last chose. A new session clears the pages, results, chat, run and URL bar, but keeps the other form settings.

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
| To change course mid-run | While it works, send: "Only the cheapest plan, please." |

The agent may fetch only sites you named in the instruction, the URL bar seed, or pages already scraped. Anything else asks you first.

## Using Mistral's hosted API

Set `SCRAPER_API_KEY` (or `MISTRAL_API_KEY`), in the environment or in a `.env` file (see below), and a **Provider** dropdown appears in the footer next to the model picker. Choose Mistral and the model list switches to the models on your account that support tool calling, with context sizes from the API. Without a key the dropdown is hidden and only LM Studio is offered.

```bash
SCRAPER_API_KEY=... uv run python -m scraper
```

The app reopens on the provider and model you used last (remembered per provider in `~/.page-chat/session.db`, and kept when you start a new session). On a first run it starts on LM Studio. Setting `SCRAPER_PROVIDER` pins the starting provider instead: `SCRAPER_PROVIDER=mistral` starts on Mistral (this needs the key). If the remembered model is no longer offered, the first one in the list is selected.

**With Mistral selected, the text of every page the model reads is sent to Mistral's servers.** The header shows "Pages are sent to Mistral" for as long as it is selected, and the app prints the same warning at startup if it starts there. The key is read from the environment or your `.env` file, and the app never writes it anywhere.

You can only switch providers while nothing is running. Your chat carries over, but a saved agent run can only continue on the provider it started on: after switching, the app shows it with a Discard button instead of Resume, and Resume comes back when you switch back.

## Privacy

With the default local provider everything stays on your machine: the model runs in LM Studio, pages and results are stored in `~/.page-chat/` (user-only permissions), and LangSmith or any other cloud tracing is switched off at startup. Scraped pages are untrusted: the agent is told never to follow instructions found inside them, and its allowlist and step budgets limit what a hostile page could make it do. That reduces the risk of prompt injection but does not remove it.

## Configuration

Settings come from environment variables, or from a `.env` file in the directory you run the app from. Copy `.env.example` to `.env` to start; git ignores `.env`, so a key kept there is not committed. Real environment variables win over the file. Values override the defaults in `src/scraper/config.py`:

| Variable | Default | Meaning |
|---|---|---|
| `SCRAPER_PROVIDER` | `lmstudio` | Provider the app starts on: `lmstudio` or `mistral`. When unset, the last-used provider is restored |
| `SCRAPER_API_KEY` | none | Mistral API key (or `MISTRAL_API_KEY`). Setting it makes Mistral available in the dropdown; required to start on `mistral` |
| `SCRAPER_BASE_URL` | `http://localhost:1234/v1` | LM Studio OpenAI-compatible endpoint |
| `SCRAPER_MISTRAL_BASE_URL` | `https://api.mistral.ai/v1` | Mistral endpoint |
| `SCRAPER_HOST` / `SCRAPER_PORT` | `127.0.0.1` / `8080` | Where the UI is served |
| `SCRAPER_DATA_DIR` | `~/.page-chat` | Where the session and run files live |
| `SCRAPER_AGENT_MAX_STEPS` | `25` | Model steps per round before the agent pauses to ask |
| `SCRAPER_AGENT_MAX_PAGES` | `30` | Page fetches per round before it pauses to ask |
| `SCRAPER_TOOL_RESULT_TOKENS` | `3000` | Size cap for each tool result |
| `SCRAPER_FALLBACK_CONTEXT` | `16384` | Context size assumed when the model is not loaded |
| `SCRAPER_CONTEXT_FRACTION` / `SCRAPER_REPLY_RESERVE` / `SCRAPER_HISTORY_RESERVE` | `0.7` / `2048` / `3000` | How much of the context window each part of a prompt may use |
| `SCRAPER_CRAWL_MAX_PAGES` / `SCRAPER_CRAWL_DEPTH` / `SCRAPER_REQUEST_DELAY` | `20` / `1` / `0.5` | Limits for the manual Follow links crawl. `SCRAPER_REQUEST_DELAY` is also the minimum pause between agent fetches: the model can raise it with `set_fetch_delay` (up to 30s) when a site pushes back, but never go below it. The chosen delay is saved with the run |
| `SCRAPER_FETCH_TIMEOUT` | `20` | Seconds per page |

The provider status in the footer re-checks every 15 seconds.

## Hosted API

`python -m scraper_api` serves the same agent to many signed-in people from one Postgres-backed process, for the Next.js web app. The web app (accounts, invitations and sign-in) is in [web/](web/README.md). Setup, settings, the required network rules and a database role layout are in [docs/hosted-deploy.md](docs/hosted-deploy.md). The local app above is unchanged.

## Tests

```bash
uv run pytest
```

The Postgres tests are skipped unless `SCRAPER_TEST_DATABASE_URL` points at a database they may fill and empty (use a scratch one). A run with it unset still reports green, so check the summary says `0 skipped` before you trust it:

```bash
podman run -d --name scraper-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_DB=scraper_test -p 127.0.0.1:55432:5432 docker.io/library/postgres:17
export SCRAPER_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/scraper_test
uv run pytest -rs
```
