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
7. **Run ended first.** If the run reaches `done`, `limit` or `error` with follow-ups still undelivered, the UI starts a normal new turn from them (joined in order, one per line) instead of dropping them. The session reports them through `Session.undelivered`.
8. **Stop.** Pressing Stop discards the queue and puts the undelivered text back in the composer as a draft. Stop means stop.
9. **Crash or restart.** Follow-ups are saved the moment they are sent. A resumed run delivers any that are still undelivered. The ones already injected live in the checkpoint and are not injected again.
10. **Queue cap.** At most 5 undelivered follow-ups. Sending a sixth is refused with "The agent has not read your earlier messages yet." and the text stays in the composer.
11. **History.** After a run that delivered follow-ups, the saved conversation reads: the original instruction, each delivered follow-up in delivery order, then the final assistant answer with its note, sources and trace. `_finish` builds one user `Message` per delivered follow-up.

## Components

### `RunRecord` (`models.py`, `store.py`)

New field `followups: list[Followup]` where `Followup = {id: str, text: str, delivered: bool}`. Persisted with the rest of the run record in `session.db`; older records without the field load as an empty list. `id` is a uuid4 hex string.

### `Session` (`session.py`)

- `steer(text: str) -> SteerResult`. Not exclusive; it does not use `_exclusive()`. Rules, in order:
  1. No run, or `run.status` not in `running` / `paused` → `SteerResult("no_run")`; the caller starts a normal turn.
  2. Blank text → `SteerResult("empty")`.
  3. Five undelivered already → `SteerResult("full")`.
  4. Otherwise append `Followup(uuid, text, False)`, save the run, return `SteerResult("queued", id)`.
- `undelivered -> list[str]`: texts of followups with `delivered` false, in order. Used by the UI after the stream ends.
- `cancel()` additionally moves undelivered texts to `Session.stopped_drafts` (list[str]) and removes them from the run, so the UI can restore them to the composer. Delivered ones stay.
- `_finish` emits the extra user messages described in Behavior 11.
- `_prepare` passes the run's follow-up inbox and an `on_followup` callback (marks delivered, widens the toolbox allowlist, saves the run) into `build_agent`.

### Follow-up middleware (`guards.py`)

New `FollowupInbox(AgentMiddleware)` placed **first** in the middleware list, so `ContextTrim` token accounting sees injected messages. Constructor takes `pending: Callable[[], list[Followup]]` and `on_followup: Callable[[Followup], None]`.

- `abefore_model` (and sync twin): take `pending()`. If empty → `None`. Otherwise return `{"messages": [HumanMessage(text, id=f.id) ...]}` and call `on_followup` for each. The explicit `id` makes a duplicate injection a no-op under the `add_messages` reducer, which covers a crash between the state commit and the delivered flag.
- `aafter_model` (and sync twin), `can_jump_to=["model"]`: if the last AI message has no tool calls and `pending()` is non-empty → `{"jump_to": "model"}`. The next `abefore_model` then injects the message. A format-correction retry from `RunGuard.after_model` takes precedence: the follow-up jump applies only when the last AI message is a valid final answer and no correction was issued. Hook order is not relied on; the hook checks `format_retries` in state, and a test pins the behavior.
- `ContextTrim` only ever trims `ToolMessage` bodies, so follow-ups are never trimmed.

### Runner (`agent.py`)

`_map` emits a new `AgentEvent("followup", text, id=...)` when a `before_model` node update contains a `HumanMessage` whose id matches a known follow-up (the middleware's update chunk). This is how the UI learns the agent has read the message. `AgentEvent` gains an optional `id` field.

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
2. The agent finishes its current step. `FollowupInbox.abefore_model` finds the follow-up, returns a `HumanMessage(id=f.id)` state update, and `on_followup` marks it delivered, widens the allowlist and saves the run.
3. The runner maps that node update to a `followup` event; the UI marks the bubble delivered.
4. The model call sees the message and adapts. If it had already produced a final answer, `aafter_model` jumped back so it can respond.
5. At the end, `_finish` writes instruction, follow-ups and final answer to history.

## Error handling

- `steer` never raises for normal conditions; it returns a result code. Persistence errors are logged and returned as `SteerResult("error")` with the text left in the composer.
- A follow-up in a crashed run is not lost: it is on the run record and delivered on resume. A duplicate injection is impossible because the message id equals the follow-up id.
- If the run ends in `error` or `limit` with undelivered follow-ups, they are handed to the UI as a new turn, so the user's text is acted on rather than discarded.

## Testing

Session and middleware (`ScriptedChat`, no real model):
- A follow-up sent after the first step appears as a user message in the next model call and not before.
- A follow-up queued during the final model call causes one extra step and is answered in the same run.
- A follow-up naming a new host widens the allowlist with no site pause.
- The sixth undelivered follow-up is refused; the first five are unaffected.
- Redelivery after a simulated crash: the message appears exactly once (message id dedupe) and `delivered` is true.
- A paused run: the follow-up is delivered with the first model call after resume.
- Stop returns undelivered text through `stopped_drafts` and leaves delivered follow-ups intact.
- A run ending with undelivered follow-ups exposes them through `undelivered`.
- History order: instruction, follow-ups, final answer.
- `steer` on an idle or finished session returns `no_run`; blank returns `empty`.
- Old `RunRecord` JSON without `followups` loads.

Runner: the `followup` event is emitted once per delivered message, with its id.

UI (`user` fixture):
- The composer stays enabled and Send stays visible during a run.
- A sent message shows a queued bubble that becomes delivered on the `followup` event.
- A submit after the run ended starts a normal turn.
- Leftover follow-ups after an ended run start a new turn; Stop restores the draft.

Real model, fixture site (manual, once): start a multi-page instruction, send a follow-up that changes the target mid-run, and check the agent's later tool calls reflect it.

## Out of scope / deferred

- Priority levels or interrupting long fetches.
