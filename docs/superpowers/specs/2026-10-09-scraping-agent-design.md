# Scraping Agent: Design

Date: 2026-10-09
Builds on: `docs/superpowers/specs/2026-10-09-web-scraper-chat-design.md` (the existing Page Chat app)

## Purpose

Turn Page Chat into an agent. The user gives plain-English instructions about what and how
to scrape ("go to X, follow the pricing links, collect every plan with its price, use stealthy
mode"). A local Qwen model, served by LM Studio, decides which pages to fetch and what to
extract by calling tools built on the existing fetch, crawl and context code. It reports in
chat and can also collect structured rows into a table that can be downloaded.

Success criteria:
- A multi-step instruction ("scrape these three pages and give me name, price, url for each") completes without further input and yields a correct results table.
- The user can steer how scraping is done in their instruction (fetch mode, CSS selector, which links to follow, which fields to collect) and the agent honours it.
- Simple questions about already-scraped pages are still answered directly, with no tool calls.
- A run is always bounded (steps, pages, domains) and can be stopped at any time.
- Failures (blocked pages, bad tool arguments, LM Studio errors) never crash the app and are shown clearly.

## Decisions (agreed with the user)

| Topic | Decision |
|---|---|
| Output | Chat answer plus an optional structured results table, downloadable as CSV or JSON. |
| Autonomy | Autonomous within limits: step budget, page budget, domain allowlist, Stop button, live step log. |
| Structure | One agent chat. The chat box takes instructions; the URL bar is an optional "start here" seed; the agent's tools fetch, list links, read, extract and add rows; follow-up questions reuse gathered pages. |
| Model | Local Qwen via LM Studio. Verified: the loaded `qwen3.8-27b-uncensored-mlx` returns valid OpenAI-style `tool_calls`. |
| Persistence | None. Sessions and results live in memory. |

## Architecture

```
src/scraper/
  agent.py     the tool loop (new)
  tools.py     tool schemas + ToolBox executing them against the session (new)
  llm.py       + stream_step(): streams tokens and assembles tool calls
  session.py   + rows, agent run, Stop; the chat entry point goes through the agent
  fetch.py     Page gains `html` (raw HTML for CSS selectors)
  config.py    + agent limits
  app.py       instruction box, step log, Stop, results table, downloads
```

`crawl.py` is reused unchanged (it still serves the optional manual scrape). `context.py` is
reused; it may gain one small helper that returns BM25 excerpts for a single page and a
focus string, used by the `focus` argument of `fetch_page` and `read_page`.

### Tools

All tool results are text. Errors are returned as text for the model to recover from, never raised.

| Tool | Arguments | Behaviour |
|---|---|---|
| `fetch_page` | `url`, `mode` (`fast`/`dynamic`/`stealthy`, default `fast`), `focus` (optional) | Domain check, then `fetch.fetch`. Stores the `Page` in the session. Returns title, URL, token count, link count and the text, capped at `tool_result_tokens`. With `focus`, only the BM25-best excerpts for it. |
| `list_links` | `url`, `contains` (optional), `limit` (default 50) | Links of a stored page, filtered by substring, same-domain flagged. |
| `read_page` | `url`, `focus` (optional), `max_tokens` (optional) | Re-reads a stored page, or only the parts relevant to `focus`. |
| `extract` | `url`, `selector`, `attr` (optional), `limit` (default 50) | Scrapling CSS selector on the stored raw HTML; returns matching text or the attribute values. |
| `add_rows` | `rows`: list of objects | Appends rows to the results table. Columns are the union of keys seen. Non-object rows are rejected with an error message. |

No `finish` tool: a model reply with no tool calls is the final answer.

### `llm.stream_step`

`stream_step(model, messages, tools) -> AsyncIterator[Event]` extends the existing streaming
client. It yields `reasoning` and `answer` events as tokens arrive, plus one `tool_calls`
event at the end of the step carrying the assembled calls (`id`, `name`, parsed `arguments`,
or an `arguments_error` string when the JSON is malformed). Tool-call fragments arrive split
across chunks and may include several calls in one step; assembly is keyed by call index.
The existing `stream_chat` stays for non-agent use and shares the error handling.

### Agent loop (`agent.py`)

`Agent.run(instruction, model) -> AsyncIterator[AgentEvent]`:

1. Build messages: system prompt (task framing, untrusted-data rule, limits, the tool list) + page context from the pages already scraped (the existing full-or-BM25 `context.build`) + recent history + the instruction.
2. Stream one step with `stream_step`. Forward `reasoning` and `answer` tokens.
3. No tool calls: the answer is final; end the run.
4. Tool calls: for each in order emit `tool_start`, execute via the ToolBox, emit `tool_result`, append the assistant message (with its `tool_calls`) and one `tool` message per call, then go to step 2.

Events: `reasoning`, `answer`, `tool_start`, `tool_result`, `rows_added`, `limit` (which limit was hit), `error`, `done`.

### Limits and safety

Defaults, each configurable:
- Steps: 25 model steps per run (`SCRAPER_AGENT_MAX_STEPS`).
- Pages: 30 fetches per run (`SCRAPER_AGENT_MAX_PAGES`).
- Tool result size: 3,000 tokens each (`SCRAPER_TOOL_RESULT_TOKENS`).
- Domain allowlist: domains of pages already in the session, of the URL bar seed, and of URLs written in the user's instruction. `www.` is equivalent to bare. Every fetch (including the post-redirect URL) is checked; others are refused with a tool error saying the user must name that site.
- Repeat guard: the same tool call with identical arguments three times in a row ends the run (`limit: repeat`).
- Context guard: when the conversation exceeds 70% of the model's context window, older tool results are replaced with a short `[trimmed]` stub. If it still does not fit, the run ends with `limit: context`.
- Stop: a flag checked between steps and tool calls. An in-flight fetch completes first.
- Prompt injection: tool results are wrapped as quoted untrusted data; the system prompt says never to follow instructions found in page content; the allowlist and budgets limit the damage of a hijacked run; no tool writes files, runs code or sends data anywhere except `add_rows`. Reduced, not eliminated.

### History and follow-ups

History keeps user instructions, final answers, and a one-line note of rows collected. Full tool traces live in the on-screen log only, so they do not consume context on later turns. An interrupted run records what was produced and is marked interrupted, as in the existing app.

### Results table

`Session.rows: list[dict]`, cleared when a new manual scrape starts a new session (an agent run continues the current session). Export helpers `rows_to_csv(rows)` and `rows_to_json(rows)` produce text: CSV uses the union of keys in first-seen order, empty string for missing values, values stringified; JSON is a list of objects.

### UI

- Instruction box placeholder: "Tell the agent what to scrape, or ask about the pages". The URL bar and Scrape button remain as an optional seed.
- The agent's turn renders a live step log: one line per tool call ("Fetched example.com/pricing, 3 links"), expandable to show arguments and the trimmed result; a step counter (for example 4/25); a Stop button replacing Send while a run is active.
- Results panel appears once rows exist: a table, Download CSV, Download JSON, row count. Downloads use NiceGUI's `ui.download`.
- The page list shows pages the agent fetched, highlighted when used.
- Visual work follows the existing theme and uses the `frontend-design` skill.

## Error handling

| Failure | Behaviour |
|---|---|
| Fetch error (403, 404, timeout, browser missing) | Returned to the model as a tool error with the existing hint (for example "try stealthy mode"); the run continues. |
| Domain not allowed | Tool error naming the allowed domains; the run continues. |
| Unknown tool, malformed or missing arguments | Tool error describing the problem; the run continues. |
| Selector matches nothing or is invalid | Tool error or "0 matches"; the run continues. |
| Step, page, repeat or context limit | Run ends with a clear message naming the limit; partial rows and answer are kept. |
| LM Studio unreachable or stream dies | Existing handling: error notice, partial output kept and marked interrupted, Regenerate offered. |
| Unexpected exception in the loop | Shown as an error notice; controls unlock. |

## Testing

pytest, test-first, no live internet and no real model in the suite.
- `llm.stream_step`: fake SSE server streams tool-call fragments split across chunks, two calls in one step, text and reasoning interleaved with calls, malformed argument JSON, empty step, plus existing error cases.
- `tools.py`: fixture pages; `extract` with selector and attribute; `list_links` filtering; `focus` excerpts; result cap; domain allowlist (instruction-named domains, `www.`, redirect off-domain); page counter; `add_rows` merging and rejection; fetch-error text.
- `agent.py`: scripted fake LLM; direct answer with no tools; fetch, extract, add_rows, answer; step budget; page budget; repeat guard; Stop mid-run; unknown tool and bad arguments; context trimming; history contents.
- `session.py` and exports: rows persistence and reset; CSV and JSON edge cases (commas, quotes, unicode, ragged rows).
- UI: one NiceGUI `user`-fixture smoke test (instruct, see steps and table, download).
- Manual: one run against the real Qwen model on a local fixture site with a two-page instruction, driven and screenshotted with Playwright.

## Configuration

New: `SCRAPER_AGENT_MAX_STEPS` (25), `SCRAPER_AGENT_MAX_PAGES` (30), `SCRAPER_TOOL_RESULT_TOKENS` (3000). No new dependencies.

## Out of scope

Saved runs or scheduling, logins, forms and click-through JavaScript interaction, parallel
tool calls, sub-agents, persistence.
