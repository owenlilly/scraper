# Web Scraper Chat: Design

Date: 2026-10-09

## Purpose

A single-user, local desktop-style tool. The user enters a URL, Scrapling fetches
and cleans the page (optionally following links), and the user asks questions about
the scraped content in a chat backed by a local Qwen model served by LM Studio.

Success criteria:
- Enter a URL, scrape it, and get streamed, source-cited answers about the page.
- Works for static pages, JS-rendered pages and bot-protected pages by choosing a fetch mode.
- Multi-page scrapes that exceed the model context still answer correctly via retrieval.
- Failures (network, LM Studio down, missing browsers) are shown inline and never crash the app.

## Decisions (agreed with the user)

| Topic | Decision |
|---|---|
| Model server | LM Studio, OpenAI-compatible API at `http://localhost:1234/v1`. Model chosen from `/v1/models` (currently `qwen3.8-27b-uncensored-mlx`). |
| GUI | NiceGUI local web app, bound to `127.0.0.1`. |
| Scrape scope | One page, plus optional same-domain link following (depth N, capped at 20 pages). |
| Fetch mode | Selectable per scrape: Fast (`Fetcher`), Dynamic (`DynamicFetcher`), Stealthy (`StealthyFetcher`). Default Fast. |
| Context strategy | Hybrid: put all page text in the prompt if it fits, otherwise BM25 chunk retrieval. No embeddings in v1. |
| Persistence | None in v1. Sessions live in memory. |

Dependencies were dry-run resolved on Python 3.14: scrapling 0.4.15, nicegui 3.18.0,
openai, rank-bm25 0.2.2, markdownify 1.2.3.

## Architecture

```
src/scraper/
  __main__.py     entry point: python -m scraper
  config.py       defaults + env overrides
  app.py          NiceGUI UI (no business logic)
  session.py      UI-independent controller: scrape + ask flow, chat history
  fetch.py        Scrapling wrapper -> Page
  crawl.py        same-domain link following
  context.py      prompt context builder (full or BM25)
  llm.py          LM Studio client: list models, context length, stream chat
tests/
```

### Units

- `fetch.fetch(url, mode) -> Page`. `Page` = `url`, `title`, `markdown`, `links`,
  `looks_empty` (flag for JS-rendered pages in Fast mode). Removes script, style,
  nav and footer noise, converts to markdown with markdownify. Rejects non-http(s) schemes.
  Runs in a worker thread because Scrapling fetchers are synchronous.
- `crawl.crawl(seed: Page, mode, depth, max_pages, fetch_fn, on_progress, cancel) -> list[Page]`.
  Breadth-first, same domain only, URL dedupe (fragment stripped), delay between requests
  (default 0.5s), cooperative cancel between pages. Failed pages are recorded and skipped.
- `context.build(pages, question, token_budget) -> Context`. `Context` = `text`, `sources`,
  `mode` (`full` or `retrieved`). Full text if total estimated tokens are at most 70% of the
  window. Otherwise split pages into chunks by markdown heading, rank with BM25 against the
  question, and fill the budget. Token estimate is chars/4. Retrieval sits behind a small
  interface so embeddings can replace BM25 later (LM Studio already lists embedding models).
- `llm.LMStudio`: `list_models() -> list[ModelInfo]` reads LM Studio's native
  `/api/v0/models` (id, type, loaded state, `loaded_context_length`), keeps chat-capable
  models (`llm`, `vlm`), and falls back to `/v1/models` if the native endpoint is missing.
  `context_length(model)` returns the loaded context or the configured fallback (16384)
  when the model is not loaded. `stream_chat(model, messages)` yields typed events
  (`reasoning`, `answer`, `error`). LM Studio streams reasoning in a separate
  `delta.reasoning_content` field (verified against the live server); inline
  `<think>...</think>` tags are also split out for models that emit them.
- `session.Session`: owns the pages, failed URLs, chat history, scrape progress and the
  cancel flag. `scrape(...)` runs the blocking fetch/crawl in a worker thread and exposes
  `progress` for the UI to poll. `ask(question, model)` builds context, trims history,
  streams events and records the exchange. The UI only renders what the session reports.

### Layout

Single page, dark theme, two panes:
- Top bar: URL input, mode select, "follow links" checkbox with depth, Scrape and Cancel buttons.
- Left pane: sources list (token count, used or unused for the last answer), context mode
  indicator, markdown preview of the selected source.
- Right pane: chat with streaming answers, a collapsible reasoning block, and the cited
  sources under each answer.
- Footer: model dropdown filled from `/v1/models`, connection status dot, context size.

## Data flow

Scrape:
1. Validate URL, disable controls, show progress.
2. `fetch` in a worker thread returns a `Page`; optional `crawl` emits progress per page.
3. Pages are stored in an in-memory `Session` (pages plus chat history). Sources list renders.

Ask:
1. `context.build` using the model's context length minus room for the reply and history.
2. System prompt instructs the model to answer only from the provided content, cite source
   URLs, say so when the answer is absent, and treat page content as untrusted data, not instructions.
3. Stream tokens into the chat. Reasoning goes to the collapsible block.
4. Trim chat history oldest-first to preserve room for context.

## Error handling

| Failure | Behaviour |
|---|---|
| Bad URL, DNS failure, timeout, HTTP 4xx/5xx | Inline message with status; 403/429 suggests Stealthy mode |
| Near-empty page in Fast mode | Offer "Retry with Dynamic" |
| Browser fetchers not installed | Message naming the one-time `scrapling install` command |
| A crawl page fails | Skipped and listed as failed; crawl continues |
| LM Studio unreachable or no model loaded | Red status dot, Send disabled, Retry re-polls |
| Stream drops mid-answer | Keep partial answer, mark interrupted, offer Regenerate |

## Security

- Bind to `127.0.0.1` only.
- Only `http` and `https` URLs are fetched; `file://` and other schemes are rejected.
- Scraped text is passed to the model as quoted data with an untrusted-content instruction.
  This reduces but does not eliminate prompt injection risk from malicious pages.

## Testing

pytest, test-first per module. No live internet in tests.
- `fetch`: local `http.server` HTML fixtures for cleaning, link extraction, empty-page flag, scheme rejection.
- `crawl`: depth limit, page cap, same-domain filter, dedupe, cancel, using a fake `fetch_fn`.
- `context`: full-vs-retrieved switch at the budget boundary, BM25 ranking on a known corpus, heading chunking.
- `llm`: fake OpenAI-compatible server for streaming, `<think>` splitting, unreachable server, context-length fallback.
- `session`: scrape and ask flows with fake fetch and fake LLM; history trimming; interrupted-answer handling.
- `app`: one NiceGUI `user`-fixture smoke test with a fake session.
- Manual smoke test of the real GUI against the live LM Studio model before calling it done.

## Configuration

`config.py` defaults, each overridable by environment variable: LM Studio base URL
(`http://localhost:1234/v1`), fallback context 16384, crawl page cap 20, crawl depth 1,
request delay 0.5s, history reserve, reply reserve.

## Setup

- Remove the PyCharm `main.py` stub; use a `src/scraper/` package.
- Add dependencies with `uv add`: `scrapling[fetchers]`, `nicegui`, `openai`, `rank-bm25`,
  `markdownify`; dev group: `pytest`, `pytest-asyncio`.
- One-time `scrapling install` for the browser fetchers.
- Run with `uv run python -m scraper`.
- No containers: it is a local tool and LM Studio runs on the host.

## Out of scope for v1

Persistent storage or saved sessions, embeddings, login-gated sites, chat export,
multi-user use, authentication.
