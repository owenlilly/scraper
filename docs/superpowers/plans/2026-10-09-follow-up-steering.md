# Follow-up Steering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user send messages while the agent is running and have the live run read them before its next model call.

**Architecture:** Follow-ups are saved on the `RunRecord` the moment they are sent. A new `FollowupInbox` middleware (last in the middleware list) injects the ones not yet in the conversation as `HumanMessage`s before each model call, and jumps back to the model if a final answer arrives while one is waiting. The runner reports each injection as a `followup` event; the session marks it delivered and the UI turns its "waiting" bubble into a normal one.

**Tech Stack:** Python 3.14, uv, LangChain `create_agent` middleware + LangGraph, NiceGUI 3.x, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-09-follow-up-steering-design.md` (read it first; this plan implements it exactly, as amended in commit 25fe522).

**Base:** branch from commit `25fe522`. The main checkout holds someone else's uncommitted work (a Mistral provider touching `agent.py`, `app.py`, `session.py`, `theme.py`, `llm.py`, `config.py`, tests and others). Do this plan in a separate git worktree created from `25fe522` so that work is neither committed nor clobbered. Reconciling the two branches later is a normal merge. Never `git add -A`; add the files each task names.

## Global Constraints

- Run tests with `unset VIRTUAL_ENV && uv run pytest` (Python 3.14, uv). Baseline: 232 tests pass at the base commit.
- Every middleware hook has a sync twin and an async twin (`abefore_model`, `aafter_model`): the app runs the graph async, tests of the sync path do not exist.
- `hook_config(can_jump_to=[...])` is required on any hook that returns `jump_to`.
- History must keep strictly alternating user and assistant `Message`s (`Session._recent_history`, `pop_interrupted` and the chat rendering pair them by position).
- Follow-up limits: at most 5 undelivered at once; at most 2000 characters each.
- `FollowupInbox` is LAST in the middleware list: `[ContextTrim, RunGuard, FollowupInbox]`.
- Follow-up dicts have exactly the keys `id` (uuid4 hex), `text`, `delivered`.
- Messages injected by the inbox carry `id=<follow-up id>` and `additional_kwargs={"followup": True}`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- UI work (Task 4) must go through the `frontend-design:frontend-design` skill, per the user's standing instruction.
- Tracing stays off; nothing here may add a network call.

## Review Focus

Failure modes the spec implies that are easy to get wrong; each has a named test.

1. The run ends while a follow-up is in flight (sent as the `done` event is yielded): the text must come back as a leftover and start a new turn, not vanish. Task 3 `test_a_follow_up_sent_as_the_run_ends_is_handed_back_as_a_leftover`.
2. A follow-up queued while the run is paused on a step/page/repeat limit must be read by the first model call after resuming (the hook ordering is what makes this work). Task 3 `test_a_follow_up_queued_during_a_limit_pause_is_delivered_when_the_run_resumes`.
3. Crash/restart around delivery must not inject twice. Task 2 (`..._queued_during_a_tool_call_is_injected_...` asserts exactly one injection while the delivered flag is never set) and Task 3 `test_a_follow_up_is_not_injected_twice_when_the_delivered_flag_is_lost_in_a_crash`.
4. History must still alternate after a follow-up, so the next turn's history is well formed. Task 3 `test_a_follow_up_sent_mid_run_reaches_the_next_model_call_and_joins_the_history_message`.
5. Stop with an unread follow-up must give the text back, not drop it, and a discarded paused run must too. Task 3 `test_stop_hands_...` and `test_discarding_...`; Task 4 UI test.

## File Structure

| File | Change |
|---|---|
| `src/scraper/store.py` | `RunRecord.followups: list[dict]` |
| `src/scraper/guards.py` | new `FOLLOWUP_KEY`, `FollowupInbox` |
| `src/scraper/agent.py` | `AgentEvent` gains kind `followup` and field `id`; `build_agent(followups=, on_inject=)`; `_map` emits the event |
| `src/scraper/session.py` | `SteerResult`, `steer`, `take_leftovers`, `take_stopped_drafts`, `cancel`/`discard_run` drafts, `_prepare` wiring, `_drive` delivered flag, `_finish` history fold, system prompt rule |
| `src/scraper/app.py` | composer enabled during a run/pause, `steer` flow, queued bubbles area, `followup` event handling, drafts and leftovers after a run |
| `src/scraper/theme.py` | queued-bubble styles |
| `tests/test_store.py`, `tests/test_session.py`, `tests/test_guards.py`, `tests/test_agent.py`, `tests/test_app.py` | tests per task |
| `README.md` | document the behavior |

---

### Task 1: Follow-ups on the run record and the `steer` API

**Files:**
- Modify: `src/scraper/store.py` (`RunRecord`)
- Modify: `src/scraper/session.py` (imports, `Session.__init__`, new methods, new `SteerResult`)
- Test: `tests/test_session.py`, `tests/test_store.py`

**Interfaces:**
- Produces (later tasks rely on these exact names):
  - `RunRecord.followups: list[dict]` where each dict is `{"id": str, "text": str, "delivered": bool}`
  - `SteerResult(status, id="")`, `status` one of `"queued" | "no_run" | "empty" | "full" | "too_long"`
  - `Session.steer(text: str) -> SteerResult`
  - `Session._undelivered() -> list[dict]` (follow-up dicts of the current run whose `delivered` is false, in send order)
  - `Session._take_undelivered() -> list[str]` (removes them from the run, saves, returns texts)
  - `Session.take_leftovers() -> list[str]` (like `_take_undelivered` but only when `run.status` is `"done"` or `"ended"`, else `[]`)
  - `Session.take_stopped_drafts() -> list[str]` (returns and clears `Session._stopped_drafts`)
  - constants `MAX_QUEUED_FOLLOWUPS = 5`, `MAX_FOLLOWUP_CHARS = 2000` in `session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session.py` (after `proceed_all`, before the first agent test; the helper `agent_session`, `run_all` and `ai`/`call` already exist there):

```python
async def paused_session(**kwargs):
    """A session whose run is paused on a question, so it is live but not streaming."""
    session, _ = agent_session({}, [ai("", call("ask_user", "c1", question="Which plan?"))], **kwargs)
    await run_all(session, "do it")
    assert session.run.status == "paused"
    return session


async def test_steer_queues_a_follow_up_on_a_live_run_and_saves_it(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    session = await paused_session(store=store)
    result = session.steer("  also the SKU  ")
    assert result.status == "queued" and result.id
    assert store.load_run().followups == [{"id": result.id, "text": "also the SKU", "delivered": False}]


async def test_steer_says_there_is_no_run_when_nothing_is_live():
    session, _ = agent_session({}, [])
    assert session.steer("hello").status == "no_run"
    session = await paused_session()
    session.run.status = "done"
    assert session.steer("hello").status == "no_run"


async def test_steer_refuses_blank_oversized_and_too_many_follow_ups():
    session = await paused_session()
    assert session.steer("   ").status == "empty"
    assert session.steer("x" * 2001).status == "too_long"
    assert session.steer("x" * 2000).status == "queued"
    for n in range(4):
        assert session.steer(f"message {n}").status == "queued"
    assert session.steer("one too many").status == "full"
    assert len(session.run.followups) == 5


async def test_leftovers_come_back_only_when_the_run_ended_for_good(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    session = await paused_session(store=store)
    session.steer("first")
    session.steer("second")
    assert session.take_leftovers() == []  # still paused: they stay queued for the agent
    assert len(session.run.followups) == 2
    session.run.status = "done"
    assert session.take_leftovers() == ["first", "second"]
    assert session.run.followups == [] and store.load_run().followups == []
    assert session.take_leftovers() == []
```

Append to `tests/test_store.py`:

```python
def test_a_run_record_saved_before_follow_ups_existed_still_loads(tmp_path):
    import json
    from dataclasses import asdict

    from scraper.store import RunRecord, SessionStore

    store = SessionStore(tmp_path / "s.db")
    data = asdict(RunRecord(number=1, instruction="x"))
    del data["followups"]
    store._run(lambda conn: conn.execute("INSERT OR REPLACE INTO run (id, data) VALUES (1, ?)", (json.dumps(data),)))
    assert store.load_run().followups == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_session.py tests/test_store.py -q -k "steer or leftovers or follow_ups_existed"`
Expected: FAIL (`AttributeError: ... 'steer'` and `KeyError`/`TypeError` on `followups`).

- [ ] **Step 3: Implement**

`src/scraper/store.py`, in `RunRecord`, after `trace`:

```python
    followups: list[dict] = field(default_factory=list)
```

`src/scraper/session.py` imports (merge with the existing import block; keep it sorted the way the file is):

```python
import uuid
from dataclasses import dataclass
from typing import Literal
```

Below `SessionBusy`:

```python
MAX_QUEUED_FOLLOWUPS = 5
MAX_FOLLOWUP_CHARS = 2000


@dataclass(frozen=True)
class SteerResult:
    status: Literal["queued", "no_run", "empty", "full", "too_long"]
    id: str = ""
```

In `Session.__init__`, after `self.busy = False`:

```python
        self._stopped_drafts: list[str] = []
```

New methods, placed right after `cancel` (which Task 3 extends):

```python
    def steer(self, text: str) -> SteerResult:
        """Queue a message for the live run to read before its next model call."""
        run = self.run
        if run is None or run.status not in ("running", "paused"):
            return SteerResult("no_run")
        text = text.strip()
        if not text:
            return SteerResult("empty")
        if len(self._undelivered()) >= MAX_QUEUED_FOLLOWUPS:
            return SteerResult("full")
        if len(text) > MAX_FOLLOWUP_CHARS:
            return SteerResult("too_long")
        followup = {"id": uuid.uuid4().hex, "text": text, "delivered": False}
        run.followups.append(followup)
        self._save_run()
        return SteerResult("queued", followup["id"])

    def _undelivered(self) -> list[dict]:
        return [f for f in self.run.followups if not f["delivered"]] if self.run is not None else []

    def _take_undelivered(self) -> list[str]:
        taken = self._undelivered()
        if taken:
            self.run.followups = [f for f in self.run.followups if f["delivered"]]
            self._save_run()
        return [f["text"] for f in taken]

    def take_leftovers(self) -> list[str]:
        """Messages the agent never read because its run ended for good; the caller sends them as a new turn."""
        if self.run is None or self.run.status not in ("done", "ended"):
            return []
        return self._take_undelivered()

    def take_stopped_drafts(self) -> list[str]:
        """Messages handed back by Stop or Discard, to restore to the composer."""
        drafts, self._stopped_drafts = self._stopped_drafts, []
        return drafts
```

- [ ] **Step 4: Run the tests to verify they pass, then the suite**

Run: `unset VIRTUAL_ENV && uv run pytest -q`
Expected: all pass (232 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add src/scraper/store.py src/scraper/session.py tests/test_session.py tests/test_store.py
git commit -m "Queue follow-up messages on the run record

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The inbox middleware, the `followup` event and the agent wiring

**Files:**
- Modify: `src/scraper/guards.py`
- Modify: `src/scraper/agent.py` (`AgentEvent`, `build_agent`, `_map`, imports)
- Test: `tests/test_guards.py`, `tests/test_agent.py`

**Interfaces:**
- Consumes: nothing from Task 1 (the middleware takes plain callables).
- Produces:
  - `scraper.guards.FOLLOWUP_KEY = "followup"`
  - `scraper.guards.FollowupInbox(pending: Callable[[], list[dict]], on_inject: Callable[[dict], None] | None = None)`
  - `build_agent(..., followups=None, on_inject=None)`: when `followups` is not `None`, `FollowupInbox(followups, on_inject)` is appended LAST to the middleware
  - `AgentEvent(kind="followup", text=<message text>, id=<follow-up id>)`; `AgentEvent` gains field `id: str = ""`

- [ ] **Step 1: Write the failing tests**

`tests/test_guards.py`: change the import line to `from scraper.guards import TRIMMED, ContextTrim, FollowupInbox, RunGuard`, then append:

```python
queue: list[dict] = []


@tool(response_format="content_and_artifact")
def probe_then_queue(url: str) -> tuple[str, dict]:
    """Probe a URL while the user types a follow-up."""
    queue.append({"id": "f1", "text": "also the SKU", "delivered": False})
    return f"probed {url}", {"ok": True, "fetched": [url], "rows_added": 0, "limit": None}


def build_with_inbox(script, pending, *, tools=(probe,), injected=None):
    model = ScriptedChat(script=list(script))
    agent = create_agent(
        model,
        tools=list(tools),
        system_prompt="x",
        checkpointer=InMemorySaver(),
        middleware=[
            ContextTrim(100_000),
            RunGuard(max_steps=25),
            FollowupInbox(pending, on_inject=injected.append if injected is not None else None),
        ],
    )
    return agent, model


def humans(call_record):
    return [m.content for m in call_record["messages"] if isinstance(m, HumanMessage)]


async def test_a_follow_up_queued_during_a_tool_call_is_injected_exactly_once_before_the_next_model_call():
    queue.clear()
    injected = []
    script = [ai("", call("probe_then_queue", "c1", url="a")), ai("done")]
    agent, model = build_with_inbox(script, lambda: list(queue), tools=(probe_then_queue,), injected=injected)
    _, state = await drive(agent, first())
    assert "also the SKU" not in humans(model.calls[0])
    assert humans(model.calls[1])[-1] == "also the SKU"
    sent = [m for m in state.values["messages"] if m.additional_kwargs.get("followup")]
    assert [(m.id, m.content) for m in sent] == [("f1", "also the SKU")]  # the flag was never set, yet no second copy
    assert [f["id"] for f in injected] == ["f1"] and len(model.calls) == 2


async def test_a_follow_up_arriving_with_the_final_answer_sends_the_run_back_to_the_model():
    calls = {"n": 0}

    def pending():
        calls["n"] += 1
        return [] if calls["n"] == 1 else [{"id": "f1", "text": "one more thing", "delivered": False}]

    agent, model = build_with_inbox([ai("First answer."), ai("Second answer.")], pending)
    _, state = await drive(agent, first())
    assert len(model.calls) == 2 and humans(model.calls[1])[-1] == "one more thing"
    assert state.values["messages"][-1].content == "Second answer."


async def test_a_reply_with_unparseable_tool_calls_is_corrected_not_mistaken_for_a_final_answer():
    calls = {"n": 0}

    def pending():
        calls["n"] += 1
        return [] if calls["n"] == 1 else [{"id": "f1", "text": "ignore me", "delivered": False}]

    agent, model = build_with_inbox([bad_call(1), ai("", call("probe", "c2", url="a")), ai("fixed")], pending)
    _, state = await drive(agent, first())
    assert state.values["messages"][-1].content == "fixed" and len(model.calls) == 3
    second = humans(model.calls[1])
    assert any("not valid JSON" in text for text in second) and second[-1] == "ignore me"
```

`tests/test_agent.py`: extend the `setup` helper signature with `followups=None` and pass it through (`build_agent(..., on_grant=lambda reason: box.grant_pages(), followups=followups)`), then append:

```python
async def test_a_delivered_follow_up_is_reported_as_an_event():
    calls = {"n": 0}

    def pending():
        calls["n"] += 1
        return [] if calls["n"] == 1 else [{"id": "f1", "text": "also the SKU", "delivered": False}]

    runner, *_ = setup([ai("", call("fetch_page", "c1", url=URL)), ai("done")], followups=pending)
    events = await collect(runner.run("t", MESSAGES))
    assert [(e.id, e.text) for e in events if e.kind == "followup"] == [("f1", "also the SKU")]
    assert events[-1].kind == "done"
```

- [ ] **Step 2: Run to verify they fail**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_guards.py tests/test_agent.py -q -k "follow_up or unparseable"`
Expected: FAIL (`ImportError: cannot import name 'FollowupInbox'`, `TypeError: build_agent() got an unexpected keyword argument 'followups'`).

- [ ] **Step 3: Implement**

`src/scraper/guards.py`, after the `CONTINUE_TEXT` dict add `FOLLOWUP_KEY = "followup"`, and append the class at the end of the file:

```python
class FollowupInbox(AgentMiddleware):
    """Hands the user's messages sent mid-run to the model, before its next call.

    The conversation state is the source of truth: a follow-up whose id is already a message in
    the state is never injected again, so a crash between the checkpoint and the delivered flag
    cannot duplicate it.
    """

    state_schema = GuardState

    def __init__(self, pending: Callable[[], list[dict]], on_inject: Callable[[dict], None] | None = None) -> None:
        self._pending = pending
        self._on_inject = on_inject

    def _waiting(self, messages) -> list[dict]:
        seen = {message.id for message in messages if message.id}
        return [followup for followup in self._pending() if followup["id"] not in seen]

    def before_model(self, state, runtime):
        waiting = self._waiting(state["messages"])
        if not waiting:
            return None
        if self._on_inject is not None:
            for followup in waiting:
                self._on_inject(followup)
        return {
            "messages": [
                HumanMessage(followup["text"], id=followup["id"], additional_kwargs={FOLLOWUP_KEY: True}) for followup in waiting
            ]
        }

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        """A final answer is not the end while the user's message is still unread: answer it in this run."""
        messages = state["messages"]
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls or last.invalid_tool_calls or not _text(last).strip():
            return None  # unparseable tool calls belong to RunGuard's correction
        return {"jump_to": "model"} if self._waiting(messages) else None

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        return self.after_model(state, runtime)
```

`src/scraper/agent.py`:

- import: `from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage` and `from .guards import FOLLOWUP_KEY, ContextTrim, FollowupInbox, RunGuard`.
- `AgentEvent.kind` Literal gains `"followup"`; add field `id: str = ""` at the end of the dataclass.
- `build_agent`:

```python
def build_agent(model, tools, *, system_prompt, checkpointer, max_steps, max_context_tokens, on_grant=None, followups=None, on_inject=None):
    middleware = [ContextTrim(max_context_tokens), RunGuard(max_steps=max_steps, on_grant=on_grant)]
    if followups is not None:
        middleware.append(FollowupInbox(followups, on_inject))  # last: a run paused inside RunGuard must still pass through it on resume
    return create_agent(
        model,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        middleware=middleware,
    )
```

- `_map`, in the `for node, update in chunk.items():` loop, directly after the `if not isinstance(update, dict): continue` guard and BEFORE the existing `if node.endswith("before_model") and "steps" in update:` chain, add:

```python
            if node.endswith("before_model"):
                for message in update.get("messages") or []:
                    if isinstance(message, HumanMessage) and message.additional_kwargs.get(FOLLOWUP_KEY):
                        yield AgentEvent("followup", _plain(message.content), id=message.id or "")
```

- [ ] **Step 4: Run to verify they pass, then the suite**

Run: `unset VIRTUAL_ENV && uv run pytest -q`
Expected: all pass. If the `followup` event test fails because the update chunk's key is not `FollowupInbox.before_model`, print `chunk` in `_map` once to read the real node name and adjust the `endswith` test accordingly (record a `Ruling:`).

- [ ] **Step 5: Commit**

```bash
git add src/scraper/guards.py src/scraper/agent.py tests/test_guards.py tests/test_agent.py
git commit -m "Add the follow-up inbox middleware and followup event

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Wire follow-ups into the session

**Files:**
- Modify: `src/scraper/session.py` (`AGENT_PROMPT`, `cancel`, `discard_run`, `_prepare`, `_drive`, `_finish`)
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: Task 1 (`steer`, `_undelivered`, `_take_undelivered`, `take_leftovers`, `take_stopped_drafts`, `RunRecord.followups`); Task 2 (`build_agent(followups=, on_inject=)`, `AgentEvent("followup", text, id=...)`).
- Produces: the end-to-end behavior in the spec; no new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session.py` (needs `from scraper.agent import open_checkpointer` and `AsyncExitStack`, which the file already imports for its restart tests; `OTHER`, `SITE_A`, `CFG`, `Config`, `make_page`, `ScriptedChat`, `FakeLLM`, `make_fetch` are already in scope):

```python
def once(fn):
    done = []

    def wrapper(session):
        if not done:
            done.append(True)
            fn(session)

    return wrapper


def steering_session(web, script, on_fetch, *, cfg=CFG, store=None, saver=None):
    """A session whose first page fetch runs `on_fetch(session)`, like a user typing while the page loads."""
    model = ScriptedChat(script=list(script))
    holder = {}
    inner = make_fetch(web)

    def fetch(url, mode="fast", *, timeout=20.0):
        on_fetch(holder["session"])
        return inner(url, mode, timeout=timeout)

    session = Session(cfg, FakeLLM(), fetch, store=store, checkpointer=saver, model_factory=lambda model_id: model)
    holder["session"] = session
    return session, model


async def test_a_follow_up_sent_mid_run_reaches_the_next_model_call_and_joins_the_history_message():
    web = {SITE_A: make_page(SITE_A)}
    script = [ai("", call("fetch_page", "c1", url=SITE_A)), ai("Cheapest is Starter."), ai("Sure.")]
    session, model = steering_session(web, script, once(lambda s: s.steer("only the cheapest plan")))
    events = await run_all(session, f"Look at {SITE_A}")
    assert events[-1].kind == "done"
    assert [e.text for e in events if e.kind == "followup"] == ["only the cheapest plan"]
    first, second = model.calls
    assert all("only the cheapest plan" not in str(m.content) for m in first["messages"])
    assert second["messages"][-1].content == "only the cheapest plan"
    folded = f"Look at {SITE_A}\n\nFollow-up: only the cheapest plan"
    assert [m.role for m in session.history] == ["user", "assistant"] and session.history[0].content == folded
    assert session.run.followups[0]["delivered"] is True
    await run_all(session, "anything else?")  # history still pairs up for the next turn
    third = model.calls[2]["messages"]
    assert [m.type for m in third] == ["system", "human", "ai", "human"] and third[1].content == folded


async def test_a_follow_up_sent_while_the_final_answer_streams_is_answered_in_the_same_run():
    session, model = agent_session({}, [ai("First answer."), ai("Second answer.")])
    sent, events = False, []
    async for event in session.run_agent("hello", "qwen-test"):
        events.append(event)
        if event.kind == "answer" and not sent:
            sent = True
            assert session.steer("one more thing").status == "queued"
    assert events[-1].kind == "done" and len(model.calls) == 2
    assert model.calls[1]["messages"][-1].content == "one more thing"
    assert session.history[1].content.strip() == "Second answer."
    assert session.history[0].content == "hello\n\nFollow-up: one more thing"


async def test_a_follow_up_that_names_a_new_site_widens_the_allowlist_without_a_pause():
    web = {SITE_A: make_page(SITE_A), OTHER: make_page(OTHER)}
    script = [ai("", call("fetch_page", "c1", url=SITE_A)), ai("", call("fetch_page", "c2", url=OTHER)), ai("Both read.")]
    session, _ = steering_session(web, script, once(lambda s: s.steer(f"also read {OTHER}")))
    events = await run_all(session, f"Look at {SITE_A}")
    assert not [e for e in events if e.kind == "needs_input"] and events[-1].kind == "done"
    assert [p.url for p in session.pages] == [SITE_A, OTHER] and "other.test" in session.run.allowed_hosts


async def test_a_follow_up_queued_during_a_limit_pause_is_delivered_when_the_run_resumes():
    web = {SITE_A: make_page(SITE_A)}
    cfg = Config(request_delay=0.0, agent_max_steps=1)
    session, model = agent_session(web, [ai("", call("fetch_page", "c1", url=SITE_A)), ai("Short answer.")], cfg=cfg)
    await run_all(session, f"Look at {SITE_A}")
    assert session.pending["kind"] == "limit"
    assert session.steer("keep it short").status == "queued"
    resumed = await resume_all(session, {"action": "wrap_up"})
    assert resumed[-1].kind == "done"
    assert model.calls[1]["messages"][-1].content == "keep it short"
    assert [e.text for e in resumed if e.kind == "followup"] == ["keep it short"]


async def test_a_follow_up_is_not_injected_twice_when_the_delivered_flag_is_lost_in_a_crash(tmp_path):
    web = {SITE_A: make_page(SITE_A)}
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        store = SessionStore(tmp_path / "session.db")
        script = [ai("", call("fetch_page", "c1", url=SITE_A))]
        session, _ = steering_session(web, script, once(lambda s: s.steer("also the SKU")), store=store, saver=saver)
        stream = session.run_agent(f"read {SITE_A}", "qwen-test")
        async for event in stream:
            if event.kind == "followup":
                break
        await stream.aclose()
        record = store.load_run()
        record.followups[0]["delivered"] = False  # the app died before the flag was saved
        store.save_run(record)
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(tmp_path / "checkpoints.db", stack)
        session, model = agent_session(web, [ai("finished")], store=SessionStore(tmp_path / "session.db"), saver=saver)
        await session.restore()
        events = await proceed_all(session)
        assert events[-1].kind == "done"
        assert [m.content for m in model.calls[0]["messages"]].count("also the SKU") == 1


async def test_stop_hands_an_unread_follow_up_back_as_a_draft():
    web = {SITE_A: make_page(SITE_A)}

    def type_then_stop(session):
        session.steer("never mind the footer")
        session.cancel()

    script = [ai("", call("fetch_page", "c1", url=SITE_A)), ai("unused")]
    session, model = steering_session(web, script, once(type_then_stop))
    events = await run_all(session, f"Look at {SITE_A}")
    assert events[-1].kind == "limit" and events[-1].text == "stopped" and session.run.status == "stopped"
    assert session.take_stopped_drafts() == ["never mind the footer"] and session.take_stopped_drafts() == []
    assert session.run.followups == [] and len(model.calls) == 1


async def test_discarding_a_paused_run_hands_unread_follow_ups_back_as_drafts():
    session = await paused_session()
    session.steer("remember the footer")
    await session.discard_run()
    assert session.run.status == "ended" and session.take_stopped_drafts() == ["remember the footer"]


async def test_a_follow_up_sent_as_the_run_ends_is_handed_back_as_a_leftover():
    session, _ = agent_session({}, [ai("All done.")])
    async for event in session.run_agent("hello", "qwen-test"):
        if event.kind == "done":
            assert session.steer("too late").status == "queued"  # the run is still "running" until it finishes up
    assert session.run.status == "done"
    assert session.take_leftovers() == ["too late"] and session.take_leftovers() == []
    assert session.history[0].content == "hello"  # never read, so not part of that turn


async def test_the_system_prompt_tells_the_agent_to_expect_new_messages():
    session, model = agent_session({}, [ai("ok")])
    await run_all(session, "hi")
    assert "may send new messages while you work" in model.calls[0]["messages"][0].content
```

- [ ] **Step 2: Run to verify they fail**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_session.py -q -k "follow_up or draft or leftover or expect_new_messages"`
Expected: FAIL (the follow-up is never injected; no `followup` events; drafts empty).

- [ ] **Step 3: Implement**

All in `src/scraper/session.py`.

1. `AGENT_PROMPT`: insert a block between the "Asking the user" block and "Limits and safety:" (keep the blank line between blocks):

```
Messages while you work:
- The user may send new messages while you work. Treat the latest one as taking priority over earlier instructions: adapt your plan to it and keep what you have already gathered.

```

2. `cancel`:

```python
    def cancel(self) -> None:
        self._cancel.set()
        if self.run is not None and self.run.status == "running":
            self._stopped_drafts.extend(self._take_undelivered())  # Stop means stop: hand unread messages back
```

3. `discard_run`: inside `with self._exclusive():`, after the `if self.run is None: return` guard and before `self.run.status = "ended"` add:

```python
            self._stopped_drafts.extend(self._take_undelivered())
```

4. `_prepare`: add two arguments to the `build_agent(...)` call, next to `on_grant=...`:

```python
            followups=self._undelivered,
            on_inject=lambda followup, toolbox=self._toolbox: toolbox.allowed_hosts.update(h for h in hosts_in(followup["text"]) if h),
```

5. `_drive`: add a branch just before `elif event.kind == "needs_input":`:

```python
                elif event.kind == "followup":
                    for followup in run.followups:
                        if followup["id"] == event.id:
                            followup["delivered"] = True
                    run.allowed_hosts = sorted(toolbox.allowed_hosts)
                    self._save_run()
```

6. `_finish`: replace `Message("user", run.instruction),` with a folded message. The `messages = [...]` block becomes:

```python
            asked = run.instruction + "".join(f"\n\nFollow-up: {f['text']}" for f in run.followups if f["delivered"])
            messages = [
                Message("user", asked),
                Message("assistant", answer, reasoning, sources, note=note, trace=trace),
            ]
```

- [ ] **Step 4: Run to verify they pass, then the suite**

Run: `unset VIRTUAL_ENV && uv run pytest -q`
Expected: all pass. If `test_a_follow_up_queued_during_a_limit_pause...` fails because the follow-up is not the last message of the second model call, the middleware order is wrong: confirm `FollowupInbox` is appended last in `build_agent` (Task 2).

- [ ] **Step 5: Commit**

```bash
git add src/scraper/session.py tests/test_session.py
git commit -m "Deliver follow-up messages to the running agent

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The composer, queued bubbles and drafts in the UI

**Files:**
- Modify: `src/scraper/app.py`, `src/scraper/theme.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `Session.steer`, `SteerResult`, `Session.take_leftovers`, `Session.take_stopped_drafts`, `AgentEvent("followup", text, id=...)`, `session.run.followups`.
- Produces: user-visible behavior only.

**Step 0 (required):** invoke the `frontend-design:frontend-design` skill before editing, because this changes the UI. Keep its output within the constraints below: queued messages sit in their own area under the chat, are visibly pending, and keep the existing visual language (`--pc-*` variables in `theme.py`, `pc-` class prefix). Refine the CSS in Step 3 as the skill advises, but keep the element structure and the `marker`s used by the tests.

- [ ] **Step 1: Write the failing tests**

In `tests/test_app.py` add `import threading` at the top, then append:

```python
def gated_session(script, web, gate):
    """A session whose page fetches wait on `gate`, so a test can type while the agent is mid-run."""
    model = ScriptedChat(script=list(script))
    inner = make_fetch(web)

    def fetch(url, mode="fast", *, timeout=20.0):
        gate.wait(10)
        return inner(url, mode, timeout=timeout)

    return Session(Config(request_delay=0.0), FakeLLM(), fetch, model_factory=lambda model_id: model), model


async def test_a_follow_up_typed_while_the_agent_works_is_queued_then_delivered(user: User):
    gate = threading.Event()
    script = [ai("", call("fetch_page", "c1", url=SITE)), ai("Starter is the cheapest.")]
    session, model = gated_session(script, {SITE: make_page(SITE)}, gate)
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Look at {SITE}")
    await user.should_see(marker="stop")  # the run is in flight, blocked on the page
    await user.should_see(marker="send")  # Send stays available beside Stop
    await send_instruction(user, "only the cheapest plan")
    await user.should_see("Waiting for the agent to read this")
    gate.set()
    await user.should_see("Starter is the cheapest.")
    await user.should_not_see("Waiting for the agent to read this")
    assert session.history[0].content.endswith("Follow-up: only the cheapest plan")
    assert model.calls[1]["messages"][-1].content == "only the cheapest plan"


async def test_a_follow_up_typed_during_a_question_is_delivered_after_the_answer(user: User):
    script = [ai("", call("ask_user", "c1", question="Which plan?", options=["Team"])), ai("Team it is.")]
    session, model = agent_session({SITE: make_page(SITE)}, script)
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Price at {SITE}")
    await user.should_see("Which plan?")
    await send_instruction(user, "be brief")
    await user.should_see("Waiting for the agent to read this")
    user.find(marker="option:Team").click()
    await user.should_see("Team it is.")
    assert model.calls[1]["messages"][-1].content == "be brief"


async def test_stopping_hands_an_unread_follow_up_back_to_the_composer(user: User):
    gate = threading.Event()
    script = [ai("", call("fetch_page", "c1", url=SITE)), ai("unused")]
    session, _ = gated_session(script, {SITE: make_page(SITE)}, gate)
    register_page(FakeLLM(), session)
    await user.open("/")
    await user.should_see("LM Studio connected")
    await send_instruction(user, f"Look at {SITE}")
    await user.should_see(marker="stop")
    await send_instruction(user, "never mind the footer")
    await user.should_see("Waiting for the agent to read this")
    user.find(marker="stop").click()
    gate.set()
    await user.should_see("Stopped")
    assert next(iter(user.find(marker="question").elements)).value == "never mind the footer"
    await user.should_not_see("Waiting for the agent to read this")
```

- [ ] **Step 2: Run to verify they fail**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_app.py -q -k "follow_up or hands_an_unread"`
Expected: FAIL (`should_see(marker="send")` finds nothing while running; typing during the run is disabled).

- [ ] **Step 3: Implement**

`src/scraper/app.py`:

1. Near `busy = {...}` add `queued: dict = {}` (follow-up id to its waiting bubble).

2. Layout: directly after the `with ui.scroll_area()... as chat_scroll:` block and before `results_box = ...`, add:

```python
            queued_box = ui.column().classes("w-full pc-queued")
```

3. `update_controls`: replace the lines from `send_btn.set_visibility(not running)` through the `hint = ...` / `question.props(...)` pair with:

```python
        stop_btn.set_visibility(running)
        steering = agent_on and (running or paused) and bool(model_select.value)
        can_ask = bool(model_select.value) and (agent_on or bool(session.pages)) and not scraping and not asking and not paused
        send_btn.set_enabled(can_ask or steering)
        question.set_enabled(steering or (not asking and not paused))
        if steering:
            hint = "Message the agent while it works"
        else:
            hint = "Tell the agent what to scrape, or ask about the pages" if agent_on else "Ask about this page"
        question.props(f'placeholder="{hint}"')
```

(delete the old `send_btn.set_visibility(not running)`, the old `can_ask`/`send_btn.set_enabled(can_ask)` and `question.set_enabled(...)` lines; `stop_btn.set_visibility(running)` stays, once.)

4. Before `render_history`, define:

```python
    def add_queued(followup_id: str, text: str) -> None:
        with queued_box:
            with ui.chat_message(sent=True, name="You").classes("pc-queued-msg") as bubble:
                ui.label(text)
                ui.label("Waiting for the agent to read this").classes("pc-queued-note")
        queued[followup_id] = bubble

    def restore_drafts(drafts: list[str]) -> None:
        if drafts:
            question.set_value(" ".join([*drafts, (question.value or "").strip()]).strip())
            queued_box.clear()
            queued.clear()
```

5. `render_history`: replace the final block

```python
            if waiting_run():
                ui.chat_message(session.run.instruction, sent=True, name="You")  # the task a paused or interrupted run belongs to
        chat_scroll.scroll_to(percent=1.0)
```

with:

```python
            if waiting_run():
                ui.chat_message(session.run.instruction, sent=True, name="You")  # the task a paused or interrupted run belongs to
                for followup in session.run.followups:
                    if followup["delivered"]:
                        ui.chat_message(followup["text"], sent=True, name="You")
        queued.clear()
        queued_box.clear()
        if waiting_run():
            for followup in session.run.followups:
                if not followup["delivered"]:
                    add_queued(followup["id"], followup["text"])
        chat_scroll.scroll_to(percent=1.0)
```

6. `new_turn`: capture the bubble and mark freshness. Change `with ui.chat_message(name="Agent"):` to `with ui.chat_message(name="Agent") as bubble:` and add `"bubble": bubble, "fresh": False` to the returned dict.

7. `consume`: add `last_step = 0` next to `reasoning = answer = ""`. At the top of the `async for event in events:` body add:

```python
                    if event.kind != "followup":
                        turn["fresh"] = False
```

In the `step` branch also add `last_step = event.step`. Add a branch (before `elif event.kind == "reasoning":`):

```python
                    elif event.kind == "followup":
                        reasoning = answer = ""
                        waiting_bubble = queued.pop(event.id, None)
                        if waiting_bubble is not None:
                            queued_box.remove(waiting_bubble)
                        if turn["fresh"]:
                            chat_box.remove(turn["bubble"])  # two messages read together share the next bubble
                        else:
                            stop_waiting()
                            turn["step"].set_text("Continued below")
                        with chat_box:
                            ui.chat_message(event.text, sent=True, name="You")
                        turn = new_turn()
                        turn["fresh"] = True
                        turn["step"].set_text(f"Step {last_step}")
                        turn_state["turn"] = turn
```

8. `run_stream`: directly after `busy["ask"] = busy["agent"] = False` add `restore_drafts(session.take_stopped_drafts())`. At the very end of the function (after the `elif outcome["limit"]:` block) add:

```python
        leftovers = [] if outcome["paused"] else session.take_leftovers()
        if leftovers:
            await start_agent("\n".join(leftovers))  # the run ended before it could read them: make them a normal turn
```

9. `discard`: after `await session.discard_run()` succeeds (after the `except` block) add `restore_drafts(session.take_stopped_drafts())` before `render_history()` is called... order: call `render_history()` first (it clears queued) then `restore_drafts(...)`. Put `restore_drafts(session.take_stopped_drafts())` right after the existing `render_history()` in `discard`.

10. Add `steer` and replace `send`:

```python
    STEER_NOTICES = {
        "full": "The agent has not read your earlier messages yet.",
        "too_long": "Keep follow-ups under 2000 characters.",
    }

    async def steer(text: str) -> None:
        result = session.steer(text)
        if result.status == "queued":
            question.set_value("")
            add_queued(result.id, text)
        elif result.status == "no_run":
            if busy["agent"]:  # the run finished a moment ago; the stream has not wound down yet
                show_notice("The run just finished. Send your message again.", info=True)
            else:
                await start_agent(text)
        elif result.status in STEER_NOTICES:
            show_notice(STEER_NOTICES[result.status], info=True)

    async def send() -> None:
        text = (question.value or "").strip()
        if not text or not model_select.value:
            return
        if agent_switch.value and (busy["agent"] or session.pending is not None):
            await steer(text)
            return
        if busy["ask"] or busy["scrape"] or session.pending is not None:
            return
        if agent_switch.value:
            await start_agent(text)
        elif session.pages:
            await run_ask(text)
```

`src/scraper/theme.py`, append to `AGENT_CSS` (adjust per the frontend-design pass):

```css
.pc-queued { padding: 0 1.25rem; max-width: 76ch; }
.pc-queued-msg { opacity: 0.7; }
.pc-queued-msg .q-message-text { border: 1px dashed var(--pc-rule); }
.pc-queued-note { font-size: 0.75rem; color: var(--pc-muted); }
```

- [ ] **Step 4: Run to verify they pass, then the suite**

Run: `unset VIRTUAL_ENV && uv run pytest -q`
Expected: all pass. Existing UI tests that asserted Send was hidden while running (search `should_not_see(marker="send")`) must be updated to the new behavior: the Stop button shows and Send stays; record any such change as a `Ruling:`.

- [ ] **Step 5: Commit**

```bash
git add src/scraper/app.py src/scraper/theme.py tests/test_app.py
git commit -m "Let the user message the agent while it works

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Document it and verify against the real model

**Files:**
- Modify: `README.md`
- Create (outside the repo, scratchpad): `steer_check.py`

**Interfaces:** consumes everything above; produces documentation and a recorded manual check.

- [ ] **Step 1: Update the README**

In `README.md` under "What it does", after the "It asks when it is stuck." bullet add:

```markdown
- **Talk to it while it works.** Type a follow-up ("skip the blog pages", "also grab the SKU") and the agent reads it before its next step, keeping what it has gathered. Your message shows as waiting until it is read. Press Stop and anything unread goes back into the box.
```

In the "Instruction cookbook" table add a row:

```markdown
| To change course mid-run | While it works, send: "Only the cheapest plan, please." |
```

- [ ] **Step 2: Run the full suite**

Run: `unset VIRTUAL_ENV && uv run pytest -q`
Expected: all pass.

- [ ] **Step 3: Check against the real model (manual, once)**

LM Studio must be running with the qwen model loaded. Serve the fixture site: `cd <scratchpad>/fx && python3 -m http.server 8765 &`. Write `steer_check.py` in the scratchpad:

```python
import asyncio

from scraper.config import Config
from scraper.llm import LMStudio
from scraper.session import Session


async def main():
    cfg = Config(request_delay=0.0)
    session = Session(cfg, LMStudio(cfg.base_url))
    sent, tools, answer = False, [], ""
    async for ev in session.run_agent("Open http://127.0.0.1:8765/plans.html and list every plan with its price.", "qwen3.8-27b-uncensored-mlx"):
        if ev.kind == "tool_start":
            tools.append(ev.name)
        elif ev.kind == "step":
            answer = ""
            if tools and not sent:
                sent = True
                print("steer:", session.steer("Only report the cheapest plan, nothing else.").status)
        elif ev.kind == "followup":
            print("model read:", ev.text)
        elif ev.kind == "answer":
            answer += ev.text
    print("tools:", tools)
    print("answer:", answer.strip())
    print("history:", session.history[0].content)


asyncio.run(main())
```

Run: `cd <repo worktree> && unset VIRTUAL_ENV && uv run python <scratchpad>/steer_check.py`
Expected: prints `steer: queued`, then `model read: Only report the cheapest plan, nothing else.`, and an answer that names only the cheapest plan (Starter, $10 on the fixture), and a history message ending in `Follow-up: Only report the cheapest plan, nothing else.`. Stop the fixture server afterwards. If the model ignores the follow-up, record that in the ledger; do not tune the prompt without telling the user.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document talking to the agent while it works

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review (done before handoff)

- **Spec coverage:** Behavior 1 (delivery point) Task 2; 2 (priority prompt) Task 3 step 1 + test; 3 (allowlist) Task 3 `..._names_a_new_site_...`; 4 (final-answer race) Task 2 + Task 3; 5 (limits untouched) by construction, covered by the limit-pause test; 6 (paused) Task 3 limit-pause test + Task 4 question test; 7 (run ended first) Task 1 `take_leftovers` + Task 3 leftover test + Task 4 `run_stream`; 8 (Stop) Task 3 + Task 4; 9 (crash) Task 3 flag-lost test; 10 (cap/size) Task 1; 11 (history fold) Task 3 first test. Components: `RunRecord`, `Session`, middleware, runner, prompt, UI all covered. UI testing bullets covered by the three UI tests; the "send after the run ended starts a normal turn" case is the pre-existing `send` path plus the `no_run` branch of `steer`.
- **Known limit (not a task):** if the app dies after LangGraph commits an injected follow-up but before the `followup` event is processed, the resumed run still reads the message (state-id filter) but its delivered flag stays false, so the folded history message omits it. The agent behaved correctly; only the transcript line is missing. Acceptable for a crash window this narrow.
- **Placeholder scan:** none; every code step has code.
- **Type consistency:** follow-up dicts `{id, text, delivered}`, `SteerResult.status` strings, `FOLLOWUP_KEY`, `build_agent(followups=, on_inject=)`, `AgentEvent.id` are used identically across tasks.
