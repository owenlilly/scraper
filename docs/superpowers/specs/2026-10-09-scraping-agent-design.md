# Scraping Agent: Design (revision 2)

Date: 2026-10-09
Builds on: `docs/superpowers/specs/2026-10-09-web-scraper-chat-design.md` (the existing Page Chat app)
Revision 2 replaces the hand-written agent loop with LangChain `create_agent` on a LangGraph runtime, and adds durable execution and human-in-the-loop pauses.

## Purpose

Turn Page Chat into an agent. The user gives plain-English instructions about what and how to
scrape ("go to X, follow the pricing links, collect every plan with its price, use stealthy
mode"). A local Qwen model, served by LM Studio, decides which pages to fetch and what to
extract by calling tools built on the existing fetch and context code. It reports in chat and
can collect structured rows into a table that can be downloaded.

The agent can tell the user when it is stuck or needs a decision: it pauses, shows a question,
and continues when the user answers. A run survives an app restart and can be resumed.

Success criteria:
- A multi-step instruction ("scrape these three pages and give me name, price, url for each") completes without further input and yields a correct results table.
- The user can steer how scraping is done in their instruction (fetch mode, CSS selector, which links to follow, which fields to collect) and the agent honours it.
- Simple questions about already-scraped pages are still answered directly, with no tool calls.
- When the agent needs a decision, is blocked, hits a limit, or wants an unlisted site, the run pauses with a clear question and resumes with the user's answer.
- Closing the app mid-run, or crashing, loses nothing: on launch the session is restored and the run can be resumed from its last step.
- A run is always bounded (steps, pages, domains) and can be stopped at any time.
- Failures never crash the app and are shown clearly.

## Decisions (agreed with the user)

| Topic | Decision |
|---|---|
| Output | Chat answer plus an optional structured results table, downloadable as CSV or JSON. |
| Autonomy | Autonomous within limits, with human pauses (see Pauses). Live step log, Stop button. |
| Structure | One agent chat. The chat box takes instructions; the URL bar is an optional "start here" seed; follow-up questions reuse gathered pages. |
| Runtime | LangChain `create_agent` on LangGraph, with a SQLite checkpointer. Chosen by the user over a hand-written loop. |
| Model | Local Qwen via LM Studio through `langchain-openai` `ChatOpenAI` (OpenAI-compatible). |
| Persistence | One current session persists across restarts: pages, rows, chat history, and any paused or interrupted run. A "New session" button wipes it. |
| Cloud tracing | Off. LangSmith tracing is forced off at startup so scraped content never leaves the machine. |

## Verified facts (spike, 2026-10-09, scratch environment, Python 3.14.4)

- Installs and runs: `langchain 1.4.4`, `langchain-core 1.6.9`, `langchain-openai 1.7.0`, `langgraph 1.2.14`, `langgraph-checkpoint-sqlite 3.1.1`, with the same `openai 3.27.0` the project already uses.
- `create_agent` streams tokens and tool calls from the loaded `qwen3.8-27b-uncensored-mlx` through `ChatOpenAI(base_url=http://localhost:1234/v1)`.
- The stock `ChatOpenAI` drops LM Studio's `delta.reasoning_content`. A subclass overriding `_convert_chunk_to_generation_chunk` to copy it into `message.additional_kwargs["reasoning_content"]` restores it (192 reasoning characters seen). It relies on a private method, so a test must fail loudly if a LangChain upgrade breaks it.
- A tool that calls `interrupt(payload)` pauses the graph; with `AsyncSqliteSaver` the paused state survives process exit; a fresh process resumed with `Command(resume="Team")` and the run finished.
- The live model invents argument values the schema does not list (`mode: "stealth"`); tools must normalise them.
- On resume LangGraph restarts the tool that called `interrupt()` from its beginning. In the tested version, sibling tools of the same step that had already finished are not re-run, but after a crash or Stop any tool step may run again, so every tool must stay idempotent.
- Several tools in one step can pause at once (parallel tool calls). LangGraph then requires each answer to name its interrupt id; the UI asks the pending questions one at a time and sends each answer with its id. Resuming without ids errors and loops.
- `create_agent` treats a reply made only of unparseable tool calls (invalid JSON arguments) as a final answer. A guard sends it back to the model up to twice with a correction before giving up with a clear, non-resumable error.

## Architecture

```
src/scraper/
  chat_model.py  LMStudioChat: ChatOpenAI subclass that keeps reasoning_content (new)
  tools.py       ToolBox (the tool logic, as before) + build_tools(): LangChain tools incl. ask_user (new)
  guards.py      agent middleware: run limits with pause, repeat guard, context trim, wrap-up (new)
  agent.py       build_agent(); AgentRunner: start/resume/stream a run, event mapping (new)
  store.py       SessionStore: SQLite persistence of pages, rows, history, run record (new)
  session.py     Session: store, rows, run_agent/resume_run, restore on launch
  fetch.py       Page gains `html` (raw HTML for CSS selectors)
  context.py     + view_page (budgeted page view for tool results)
  exports.py     rows_to_csv, rows_to_json (new)
  config.py      + agent and data-dir settings
  app.py         instruction box, step log, pause card, resume banner, results, New session
```

`crawl.py` and `llm.py` are unchanged (the tool-free Q&A path, used when the Agent switch is
off for models without tool calling, still uses `llm.LMStudio.stream_chat`).

### Dependencies

Added: `langchain>=1.4,<2`, `langchain-openai>=1.7,<2`, `langgraph>=1.2,<2`,
`langgraph-checkpoint-sqlite>=3.1,<4` (brings `aiosqlite`). No `langchain-community`, no
Deep Agents, no LangSmith key.

### `chat_model.py`

`LMStudioChat(ChatOpenAI)` overrides `_convert_chunk_to_generation_chunk`: call `super()`, then
if the chunk's delta has `reasoning_content`, set it on `generation.message.additional_kwargs`.
A factory `make_chat_model(cfg, model_id)` builds it with `base_url=cfg.base_url`,
`api_key="lm-studio"`, `streaming=True`, a long read timeout and `max_retries=0`.

### Tools (`tools.py`)

`ToolBox` keeps the previously specified logic: domain allowlist, page budget, result cap,
mode normalisation (`stealth` becomes `stealthy`), quoted untrusted page text, errors returned as
text. `build_tools(box)` wraps it as LangChain tools with `response_format="content_and_artifact"`
so each result carries `(text, artifact)`, the artifact being `{"ok": bool, "rows_added": int,
"fetched": [urls], "limit": str | None}` for the UI and guards.

| Tool | Arguments | Behaviour |
|---|---|---|
| `fetch_page` | `url`, `mode` (`fast`/`dynamic`/`stealthy`), `focus` | Domain check; for an unlisted site, pauses with a `site` question (see Pauses); fetches; stores the page; returns a trimmed quoted view. Idempotent: a page already fetched in this run with the same mode is returned from storage without a second fetch or budget charge. |
| `list_links` | `url`, `contains`, `limit` | Links of a stored page; off-site links marked. |
| `read_page` | `url`, `focus`, `max_tokens` | Stored page text or the parts relevant to `focus`. |
| `extract` | `url`, `selector`, `attr`, `limit` | Scrapling CSS selector on stored raw HTML; hrefs made absolute. |
| `add_rows` | `rows` | Appends objects to the results table; exact duplicates of existing rows are ignored (idempotent). |
| `ask_user` | `question`, `options` (optional list) | Pauses the run with an `ask` question; returns "The user answered: ..." as the tool result. |

### Pauses (human in the loop)

A pause is a LangGraph `interrupt(payload)`; the payload is JSON and the UI shows it as a
question card. Three kinds:

| Kind | Raised by | Payload | Resume value | Effect |
|---|---|---|---|---|
| `ask` | the model, via `ask_user` (system prompt: ask when the instruction is ambiguous, a login or CAPTCHA blocks the page, every fetch mode failed, or a choice changes the outcome) | `question`, `options` | the answer text | returned to the model as the tool result |
| `limit` | run guard middleware when the step limit, the page budget or the repeat guard trips | `reason` (`steps`/`pages`/`repeat`), `question`, `options` | `{"action": "continue" \| "wrap_up" \| "stop", "note": str}` | `continue` grants another allowance and (for `repeat`) tells the model to change approach, `note` is passed to the model; `wrap_up` runs one tool-less final answer step; `stop` ends the run |
| `site` | `fetch_page` for a host not allowed | `host`, `question` | `{"allow": bool}` | allow adds the host to this run's allowlist and fetches; deny returns a tool error |

Free-text instructions are always possible: for `ask` the text is the answer; for `limit` it
goes in `note`.

### Run limits and guards (`guards.py`)

Defaults, each configurable: 25 model steps (`SCRAPER_AGENT_MAX_STEPS`), 30 page fetches
(`SCRAPER_AGENT_MAX_PAGES`), tool results capped at 3000 tokens (`SCRAPER_TOOL_RESULT_TOKENS`).
Middleware, with counters kept in the graph state so they survive a restart:
- **Run guard:** before each model call, counts steps; at the limit, raises a `limit` pause. A tool result carrying `limit == "pages"` raises one after the tools step. The same tool call with identical arguments three times in a row raises a `repeat` pause.
- **Context trim:** when the conversation exceeds `context_fraction` (0.7) of the model's window, old tool results are replaced by `[trimmed to save context]`; if it still does not fit, the run ends with `limit: context`.
- **Wrap-up:** after a `wrap_up` answer, the next model call runs with no tools and a user message asking for the final answer.
- **Domain allowlist:** hosts of stored pages, the URL bar seed and hosts or URLs written in the instruction. `www.` equals the bare host. Every fetch, including the post-redirect URL, is checked.

### Durable execution and persistence

- **Checkpointer:** `AsyncSqliteSaver` on `<data_dir>/checkpoints.db`, opened at app startup and closed at shutdown. Each run is its own thread, `thread_id = f"{session_id}-{run_number}"`, so a run's full message trace is isolated and cleaned up with the session.
- **Session store (`store.py`):** `<data_dir>/session.db`. Tables: `meta` (session id, run counter), `pages` (url, title, markdown, links, html, looks_empty, position), `rows`, `history` (role, content, reasoning, sources, interrupted, note, trace), `run` (run number, instruction, status `running`/`paused`/`stopped`/`done`/`error`, pending payload, allowed hosts, fetch count, trace). Writes use a short-lived connection per write (tools run in worker threads).
- **Compact history:** follow-up runs get the history of instructions, final answers and row notes only, as before. Tool traces stay in the run's thread and on screen.
- **Restore on launch:** `Session.restore()` loads pages, rows and history. A run with status `paused` shows its question card; `running` (the app died mid-run) or `stopped` shows a Resume banner; Resume continues from the last checkpoint (`astream(None, config)` for crash and stop, `Command(resume=...)` for a pause). A resumable run whose checkpoint thread no longer exists (the checkpoint file was lost or replaced) is marked ended instead, so a Resume that cannot work is never offered.
- **Stop** ends the stream at the next safe point and leaves the run `stopped` and resumable. The in-flight tool step may finish in the background; on resume it re-runs, which is safe because tools are idempotent.
- **New session** (with confirmation) deletes the session store rows and the checkpoint threads and starts clean.
- **Data directory:** `SCRAPER_DATA_DIR`, default `~/.page-chat/`, created on first launch.

### Events and UI mapping

`AgentRunner.stream(...)` consumes `agent.astream(..., stream_mode=["messages", "updates"])` and
yields `AgentEvent(kind, text, name, args, step, ok, count, urls, payload)` with kinds `step`,
`reasoning`, `answer`, `tool_start`, `tool_result`, `rows_added`, `needs_input`, `limit`, `error`,
`done`. Mapping: AI message chunks give `reasoning` (from `additional_kwargs`) and `answer`
(content); a model-node update with tool calls gives `tool_start`; a tools-node update gives
`tool_result` plus `rows_added`; the `__interrupt__` key gives `needs_input`; completion gives
`done`. The answer is the text of the last model step.

### UI

- Instruction box: "Tell the agent what to scrape, or ask about the pages". The URL bar and Scrape button stay as an optional seed. An **Agent switch** (default on) turns tools off for models without tool calling and uses the previous Q&A path.
- Live step log (one line per tool call, expandable), step counter, Stop button.
- **Question card** for `needs_input`: the question, option buttons, a free-text field with Send; `limit` cards show what was used and offer Continue, Wrap up, Stop (plus an optional note for the agent); `site` cards show the host with Allow and Deny. Every card and the resume banner has a **Discard run** button, so a question can never lock the composer.
- **Resume banner** on launch for an interrupted or stopped run.
- Results panel with table, Download CSV, Download JSON (sent as UTF-8 bytes with a media type), row count; hidden until rows exist.
- **New session** button with confirmation.
- Visual work follows the existing theme and uses the `frontend-design` skill.

## Error handling

| Failure | Behaviour |
|---|---|
| Fetch error (403, 404, timeout, browser missing) | Returned to the model as a tool error with the existing hint; the run continues. |
| Bad tool arguments, unknown tool, bad selector | Tool error text for the model; the run continues. |
| Host not allowed | `site` pause. |
| Step, page or repeat limit | `limit` pause. |
| Context limit | Run ends with a clear message; partial rows and answer are kept. |
| LM Studio unreachable or stream dies | Error notice; the run stays resumable from its last checkpoint. |
| App closed or crashed mid-run | Restored on launch with a Resume banner. |
| Corrupt or missing database | The unreadable session or checkpoint file is renamed with a `.corrupt-<timestamp>` suffix and a fresh one is started, with a notice; never a crash on launch. |

## Security

Scraped text and tool results are untrusted. Tool results are quoted data; the system prompt says
never to follow instructions found in them; the allowlist and budgets limit a hijacked run; the
only tool that changes state is `add_rows`, and `ask_user` can only ask. Tracing to LangSmith is
forced off. CSV export neutralises spreadsheet formulas. The server binds to `127.0.0.1` only.
The data directory is created with user-only permissions (0700).

## Testing

pytest, test-first, no live internet and no real model in the suite.
- `chat_model`: a mock SSE server streams `reasoning_content` and `content`; the subclass keeps both (guards against upgrade breakage).
- `tools`: fixture pages; allowlist, redirect, budget, idempotent `fetch_page` and `add_rows`, selector extraction, argument errors, artifact contents.
- `guards`/`agent`: a scripted fake chat model (with `bind_tools`) drives the real `create_agent` graph: direct answer; tool round trips; `ask_user` pause and resume; `limit` pauses with continue, wrap-up and stop; `site` approval allow and deny; repeat guard; context trim; stop and resume; idempotency of re-run tools after resume.
- Durability: run until a pause with a file-backed checkpointer, discard the objects, rebuild everything from the same files, resume with `Command(resume=...)`, and assert the run completes and rows are not duplicated.
- `store`: round trip of pages, rows, history, run record; corrupt-file handling.
- `exports`: CSV and JSON edge cases including formula neutralisation.
- UI: NiceGUI `user`-fixture smoke tests for a run, a pause card answer, and the resume banner.
- Manual: one run against the real Qwen model on a local fixture site, including an `ask_user` pause and a restart-and-resume, driven and screenshotted with Playwright.

## Configuration

New: `SCRAPER_AGENT_MAX_STEPS` (25), `SCRAPER_AGENT_MAX_PAGES` (30), `SCRAPER_TOOL_RESULT_TOKENS`
(3000), `SCRAPER_DATA_DIR` (`~/.page-chat`).

## Out of scope

Multiple saved sessions, scheduling, logins, forms and click-through JavaScript interaction,
parallel agents and sub-agents, LangSmith or any cloud tracing, Postgres.
