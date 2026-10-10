# Follow-up Steering: Design

Date: 2026-10-09
Builds on: `docs/superpowers/specs/2026-10-09-scraping-agent-design.md` (the LangChain + LangGraph agent)

## Purpose

Let the user type a message while the agent is working and have the running agent act on it.
Today the Send button is hidden for the whole run and a second run is refused with `SessionBusy`,
so correcting course ("skip the blog pages", "also grab the SKU") means pressing Stop and losing
the agent's plan, or waiting until it finishes.

Success criteria:
- A message sent mid-run reaches the live agent before its next model call and changes what it does, without losing the pages, rows or plan gathered so far.
- Nothing typed is ever lost: not on a crash, a restart, a Stop, a pause, or a run that ends while the message is in flight.
- Messages are never duplicated, whatever the crash or resume path.
- The user can see whether the agent has read a message yet.
- Existing guarantees hold: bounded runs, allowlist, one exclusive run or scrape at a time, resume after restart.

## Decisions (agreed with the user)

| Topic | Decision |
|---|---|
| What a mid-run message does | **Steer the live run.** It is injected into the running agent and seen before its next model step. If the run has already ended it becomes a normal new turn. |
| Delivery mechanism | An inbox on the run, drained by middleware in front of the model call (approach A). Aborting the stream and patching the checkpoint (approach B) was rejected: it kills in-flight generations and fights the stream and exclusivity logic. |

## Non-goals

- Interrupting a generation or fetch that is already under way. A message takes effect at the next model step, so the delay is at most one step.
- Answering a pause card (question, site approval, limit) by typing in the composer. The card remains the way to continue.
- Editing or cancelling a queued message.
- Steering in tool-free Q&A mode (Agent switch off) or during a scrape; the composer stays disabled there.

## Behavior

1. **Delivery point.** Queued messages enter the conversation as user messages immediately before the next model call. They are never placed between an assistant tool call and its tool results: the inbox is read by a `before_model` hook, which runs after the tool node finishes.
2. **Priority.** The system prompt gains one rule: the user may send messages while the agent works; the latest one takes priority over earlier instructions, and the agent should adapt its plan rather than restart it.
3. **Allowlist.** Hosts named in a follow-up (`hosts_in(text)`) are added to the allowed hosts when it is delivered, exactly as for the first instruction. The widened set is saved on the run.
4. **Final answer racing a message.** If the model produces a final answer (no tool calls) while an undelivered follow-up exists, the run does not end: it jumps back to the model with the follow-up, so the same run answers it. The earlier answer text stays visible in the stream; only the last answer is recorded in history.
5. **Limits.** A follow-up does not reset step, page or repeat counters, and does not lift a "wrap up" the user chose at a limit pause. It is delivered like any other message and the agent answers within the remaining allowance.
6. **Paused run.** While paused on a question, site approval or limit, a message is queued and delivered with the first model call after the user resolves the pause.
7. **Run ended first.** If the run ends for good (status `done` or `ended`) with follow-ups still undelivered, the UI starts a normal new turn from them (joined in order, one per line) instead of dropping them. `Session.take_undelivered()` returns and removes them. If the run ends in a resumable `error`, they stay queued on the run, still shown as queued, and are delivered when the user presses Resume.
8. **Stop.** Pressing Stop discards the queue and puts the undelivered text back in the composer as a draft. Stop means stop: this also holds for messages typed after Stop while a fetch is still finishing, for Stop on a limit card, and when a new instruction replaces a resumable run (its queued messages come back as drafts rather than vanishing).
9. **Crash or restart.** Follow-ups are saved the moment they are sent. A resumed run delivers any that are still undelivered. The ones already injected live in the checkpoint and are not injected again.
10. **Queue cap and size.** At most 5 undelivered follow-ups. Sending a sixth is refused with "The agent has not read your earlier messages yet." and the text stays in the composer. A follow-up longer than 2000 characters is refused with "Keep follow-ups under 2000 characters." (the context trimmer never sees these messages in time to shrink them, so their size is bounded at the door).
11. **History.** History must keep strictly alternating user and assistant messages (`_recent_history`, `pop_interrupted` and the chat rendering pair them by position). After a run that delivered follow-ups, `_finish` records ONE user `Message` whose content is the instruction followed by each delivered follow-up in delivery order, each introduced by a blank line and `Follow-up: `, then the final assistant answer with its note, sources and trace.

## Components

### `RunRecord` (`models.py`, `store.py`)

New field `followups: list[Followup]` where `Followup = {id: str, text: str, delivered: bool}`. Persisted with the rest of the run record in `session.db`; older records without the field load as an empty list. `id` is a uuid4 hex string.

### `Session` (`session.py`)

- `steer(text: str) -> SteerResult`. Not exclusive; it does not use `_exclusive()`. Rules, in order:
  1. No run, or `run.status` not in `running` / `paused` → `SteerResult("no_run")`; the caller starts a normal turn.
  2. Blank text → `SteerResult("empty")`.
  3. Five undelivered already (checked before the length rule) → `SteerResult("full")`.
  4. More than 2000 characters → `SteerResult("too_long")`.
  5. Otherwise append `{id, text, delivered: false}`, save the run, return `SteerResult("queued", id)`.
- `take_undelivered() -> list[str]`: returns the texts of follow-ups with `delivered` false, in order, and removes them from the run (so a later Resume cannot deliver them a second time). Used by the UI after a run ends for good.
- `cancel()` additionally, when a run is `running`, moves undelivered follow-ups out of the run into a drafts list; `take_stopped_drafts() -> list[str]` returns and clears it so the UI can restore them to the composer. Delivered ones stay.
- `_finish` emits the extra user messages described in Behavior 11.
- `_prepare` passes the run's undelivered follow-ups and an `on_inject` callback (widens the toolbox allowlist; idempotent) into `build_agent`.
- `_drive` marks a follow-up `delivered` and saves the run when the runner's `followup` event arrives, i.e. after LangGraph committed the injected message. The delivered flag is bookkeeping for the UI, the queue cap and history; it is never what prevents a double injection.

### Follow-up middleware (`guards.py`)

New `FollowupInbox(AgentMiddleware)` placed **last** in the middleware list: `[ContextTrim, RunGuard, FollowupInbox]`. Last matters because a run paused inside `RunGuard.before_model` resumes by re-running only that node, so a hook placed before it would not run again before the first model call after the pause. Constructor takes `pending: Callable[[], list[dict]]` (EVERY follow-up on the run, each `{id, text, delivered}`), `on_inject: Callable[[dict], None] | None` and `on_present: Callable[[dict], None] | None`.

- Waiting means: its id is not already the id of a message in `state["messages"]`, whatever its delivered flag says. The conversation state is the source of truth: a duplicate injection is impossible, and a message whose checkpoint was lost (LangGraph's async durability drops the latest checkpoint when a stream is closed) is injected again on resume. When a follow-up is already in the state but its flag is unset, `on_present` lets the session mark it delivered, so history and leftovers stay right.
- `abefore_model` (and sync twin): if nothing is pending → `None`. Otherwise return `{"messages": [HumanMessage(text, id=f["id"], additional_kwargs={"followup": True}) ...]}` and call `on_inject` for each (the session widens the allowlist there, before the model can call a tool).
- `aafter_model` (and sync twin), `can_jump_to=["model"]`: if the last message is an AI message with no tool calls, no invalid tool calls and non-empty text, and something is pending → `{"jump_to": "model"}`. The next `abefore_model` then injects the message. A reply with invalid tool calls is left to `RunGuard.after_model` (format retry, else it ends as an error and the follow-up stays queued for Resume); a test pins this.
- `ContextTrim` only ever trims `ToolMessage` bodies, so follow-ups are never trimmed.

### Runner (`agent.py`)

`_map` emits a new `AgentEvent("followup", text, id=...)` for each `HumanMessage` carrying `additional_kwargs["followup"]` in a `before_model` node update. This is how the UI learns the agent has read the message. `AgentEvent` gains an optional `id` field.

### System prompt (`session.py`)

Append to `AGENT_PROMPT`: "The user may send new messages while you work. Treat the latest one as taking priority over earlier instructions: adapt your plan to it, and keep what you have already gathered."

### UI (`app.py`, `theme.py`)

Built under the `frontend-design` skill, as UI work.

- While `busy["agent"]` is true the composer is enabled and Send is visible beside Stop. The placeholder reads "Message the agent while it works". During scrape, tool-free Q&A, or when the Agent switch is off, behavior is unchanged.
- Submitting during a run calls `session.steer(text)`:
  - `queued` → add a *queued* user bubble to the stream (visibly pending, e.g. muted with a "waiting for the agent" caption), clear the composer.
  - `full` → keep the text, show a notice.
  - `no_run` → fall through to `start_agent(text)` as a normal turn.
- On a `followup` event the matching bubble loses its queued styling and the step log notes "Read your message".
- After a run's stream ends, if `session.undelivered` is non-empty, start a new turn from those texts. If `session.stopped_drafts` is non-empty, put them back in the composer.
- A paused run: the composer is enabled for follow-ups; the pause card is unchanged and still the only way to resume.
- Bubbles for queued messages are restored on reload from `run.followups` (undelivered ones show as queued).

## Data flow

1. User submits text during a run → `Session.steer` → follow-up saved on the run (`delivered=false`) → UI shows a queued bubble.
2. The agent finishes its current step. `FollowupInbox.abefore_model` finds the follow-up, returns a `HumanMessage(id=f.id)` state update, and `on_inject` widens the allowlist.
3. The runner maps that committed node update to a `followup` event; `Session._drive` marks the follow-up delivered and saves the run, and the UI marks the bubble delivered.
4. The model call sees the message and adapts. If it had already produced a final answer, `aafter_model` jumped back so it can respond.
5. At the end, `_finish` writes instruction, follow-ups and final answer to history.

## Error handling

- `steer` never raises for normal conditions; it returns a result code. Persistence errors are logged and returned as `SteerResult("error")` with the text left in the composer.
- A follow-up in a crashed run is not lost: it is on the run record and delivered on resume. A duplicate injection is impossible because the message id equals the follow-up id.
- If the run ends for good (`done` or `ended`) with undelivered follow-ups, they are handed to the UI as a new turn, so the user's text is acted on rather than discarded. If it ends in a resumable `error`, they stay queued and are delivered on Resume.

## Testing

Session and middleware (`ScriptedChat`, no real model):
- A follow-up sent after the first step appears as a user message in the next model call and not before.
- A follow-up queued during the final model call causes one extra step and is answered in the same run.
- A follow-up naming a new host widens the allowlist with no site pause.
- The sixth undelivered follow-up and one over 2000 characters are refused; the first five are unaffected.
- History keeps alternating user and assistant messages after a run with follow-ups, and a later turn's history pairs correctly.
- Redelivery after a simulated crash: the message appears exactly once (state-id filter) and `delivered` is true.
- A paused run: the follow-up is delivered with the first model call after resume.
- Stop returns undelivered text through `stopped_drafts` and leaves delivered follow-ups intact.
- A run ending for good with undelivered follow-ups exposes them through `take_undelivered`; one ending in a resumable error keeps them queued and delivers them on Resume.
- History order: instruction, follow-ups, final answer.
- `steer` on an idle or finished session returns `no_run`; blank returns `empty`.
- Old `RunRecord` JSON without `followups` loads.

Runner: the `followup` event is emitted once per delivered message, with its id.

UI (`user` fixture):
- The composer stays enabled and Send stays visible during a run.
- A sent message shows a queued bubble that becomes delivered on the `followup` event.
- A submit after the run ended starts a normal turn.
- Leftover follow-ups after an ended run start a new turn; Stop restores the draft. Queued bubbles are kept in their own area beneath the chat and move into the chat, followed by a new assistant bubble, when delivered, so the transcript stays in chronological order.

Real model, fixture site (manual, once): start a multi-page instruction, send a follow-up that changes the target mid-run, and check the agent's later tool calls reflect it.

## Out of scope / deferred

- Priority levels or interrupting long fetches.
