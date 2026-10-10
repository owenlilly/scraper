"""NiceGUI front end. Renders Session state and forwards user actions; no business logic."""

import json
from collections import deque
from contextlib import aclosing

from nicegui import ui

from .config import Config
from .context import estimate_tokens
from .exports import rows_to_csv, rows_to_json
from .fetch import FetchError, Page
from .llm import LLMUnavailable, ModelInfo
from .session import Session, SessionBusy
from .theme import apply_theme

MODE_LABELS = {
    "fast": "Fast (HTTP)",
    "dynamic": "Dynamic (browser)",
    "stealthy": "Stealthy (anti-bot)",
}
LIMIT_MESSAGES = {
    "stopped": "Stopped.",
    "context": "The conversation no longer fits the model's context window, so the run ended. Try a narrower instruction.",
}


def describe_call(name: str, args: dict) -> str:
    url = args.get("url", "")
    if name == "fetch_page":
        return f"Fetching {url}" + (f" ({args['mode']})" if args.get("mode") else "")
    if name == "list_links":
        return f"Listing links on {url}"
    if name == "read_page":
        return f"Reading {url}"
    if name == "extract":
        return f"Extracting {args.get('selector', '')} from {url}"
    if name == "add_rows":
        return f"Adding {len(args.get('rows') or [])} row(s)"
    if name == "set_fetch_delay":
        return f"Setting the fetch delay to {args.get('seconds', '?')}s" + (f": {args['reason']}" if args.get("reason") else "")
    if name == "ask_user":
        return "Asking you a question"
    return name


def shorten(text: str, limit: int = 1500) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n[shortened]"


def table_cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value

def build_ui(cfg: Config, session: Session) -> None:
    apply_theme()
    ui.dark_mode(True)
    models: dict[str, ModelInfo] = {}
    busy = {"scrape": False, "ask": False, "agent": False}
    queued: dict = {}  # follow-up id -> its waiting bubble
    syncing = {"models": False}  # True while the app itself rewrites the model list

    saved_mode = session.setting("mode", "fast")
    saved_mode = saved_mode if saved_mode in MODE_LABELS else "fast"
    saved_depth = session.setting("depth", str(cfg.crawl_depth))
    saved_depth = int(saved_depth) if saved_depth.isdigit() and 1 <= int(saved_depth) <= 3 else cfg.crawl_depth

    # ---- top bar -------------------------------------------------------------
    with ui.header(elevated=False).classes("items-center gap-3 px-4 py-2 pc-header"):
        ui.label("Page Chat").classes("pc-wordmark")
        with ui.row().classes("items-center gap-1 pc-hosted").mark("hosted-notice") as hosted_chip:
            ui.icon("cloud_upload", size="1rem")
            hosted_label = ui.label()
            hosted_tip = ui.tooltip()
        url_input = (
            ui.input(placeholder="https://example.com", value=session.setting("url", ""))
            .props("dense outlined clearable")
            .classes("grow")
            .mark("url")
        )
        mode_select = ui.select(MODE_LABELS, value=saved_mode).props("dense outlined")
        follow = ui.checkbox("Follow links", value=session.setting("follow", "0") == "1")
        depth = ui.number("Depth", value=saved_depth, min=1, max=3, precision=0).props("dense outlined").classes("w-20")
        depth.bind_visibility_from(follow, "value")
        scrape_btn = ui.button("Scrape", icon="travel_explore").mark("scrape")
        cancel_btn = ui.button("Cancel", icon="close").props("flat").mark("cancel")
        cancel_btn.set_visibility(False)
        new_btn = ui.button(icon="restart_alt").props('flat round aria-label="New session"').mark("new-session")
        new_btn.tooltip("Start a new session")

    progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
    progress.set_visibility(False)

    # ---- body ----------------------------------------------------------------
    with ui.splitter(value=34).classes("w-full grow") as split:
        with split.before, ui.column().classes("pc-left w-full gap-0"):
            ui.label("Pages").classes("pc-pane-title")
            sources_box = ui.column().classes("w-full gap-0")
            context_label = ui.label("").classes("pc-hint")
            ui.separator()
            ui.label("What the model reads").classes("pc-pane-title")
            with ui.scroll_area().classes("w-full h-[calc(100vh-26rem)] min-h-[12rem]"):
                preview = ui.markdown("Scraped text appears here, exactly as the model receives it.").classes("pc-preview")
        with split.after, ui.column().classes("pc-right w-full gap-0"):
            with ui.scroll_area().classes("w-full pc-chat-scroll") as chat_scroll:
                chat_box = ui.column().classes("w-full gap-2 pc-chat")
            queued_box = ui.column().classes("w-full pc-chat pc-queued")
            results_box = ui.column().classes("w-full pc-results")
            notice_box = ui.column().classes("w-full pc-notices")
            pause_box = ui.column().classes("w-full pc-pause")
            with ui.row().classes("w-full items-center pc-compose"):
                agent_switch = ui.switch("Agent", value=session.setting("agent", "1") == "1").props("dense").mark("agent")
                question = ui.input().props("outlined dense").classes("grow").mark("question")
                send_btn = ui.button(icon="send").props('aria-label="Send"').mark("send")
                stop_btn = ui.button(icon="stop").props('flat color=negative aria-label="Stop"').mark("stop")
                stop_btn.set_visibility(False)

    # ---- footer --------------------------------------------------------------
    with ui.footer().classes("items-center gap-3 px-4 py-1 pc-footer"):
        status_dot = ui.icon("circle", size="xs").classes("pc-dot pc-dot--bad")
        status_label = ui.label(f"Connecting to {session.llm.name}...").classes("text-xs")
        provider_select = (
            ui.select({key: client.name for key, client in session.providers.items()}, value=session.provider, label="Provider")
            .props("dense borderless")
            .classes("min-w-32")
            .mark("provider")
        )
        provider_select.set_visibility(len(session.providers) > 1)
        model_select = ui.select({}, label="Model").props("dense borderless").classes("min-w-64").mark("model")
        ctx_label = ui.label("").classes("text-xs")

    # ---- helpers -------------------------------------------------------------
    def update_controls() -> None:
        scraping, asking, running = busy["scrape"], busy["ask"], busy["agent"]
        paused = session.pending is not None
        agent_on = bool(agent_switch.value)
        scrape_btn.set_enabled(not scraping and not asking and not paused)
        cancel_btn.set_visibility(scraping and bool(follow.value))
        progress.set_visibility(scraping)
        ticker.active = scraping
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

    def update_progress() -> None:
        done, total = session.progress
        progress.set_value(done / total if total else 0)

    ticker = ui.timer(0.25, update_progress, active=False)

    def show_notice(message: str, *, hint: str | None = None, action=None, action_label: str = "Retry", info: bool = False) -> None:
        notice_box.clear()
        with notice_box, ui.element("div").classes("pc-notice pc-notice--info" if info else "pc-notice"):
            ui.label(message).classes("pc-notice-title")
            if hint:
                ui.label(hint).classes("pc-notice-hint")
            with ui.row():
                if action:
                    ui.button(action_label, on_click=action).props("flat")
                ui.button("Dismiss", on_click=notice_box.clear).props("flat")

    def show_preview(page: Page) -> None:
        preview.set_content(f"{page.url}\n\n---\n\n{page.markdown}")

    def refresh_sources() -> None:
        used = set(session.last_context.sources) if session.last_context else set()
        sources_box.clear()
        with sources_box:
            for page in session.pages:
                is_used = page.url in used
                with ui.item(on_click=lambda p=page: show_preview(p)).classes("w-full pc-source" + (" pc-source--used" if is_used else "")):
                    with ui.item_section():
                        ui.label(page.title).classes("pc-source-title")
                        meta = f"{page.url}, about {estimate_tokens(page.markdown):,} tokens"
                        ui.label(meta + (", used in the last answer" if is_used else "")).classes("pc-source-meta")
            if not session.pages:
                ui.label("No pages yet.").classes("pc-hint")
            for url, why in session.failed:
                ui.label(f"Could not fetch {url}: {why}").classes("pc-failed")
        if session.last_context:
            context_label.set_text(f"Last answer drew on {len(session.last_context.sources)} page(s)")
        else:
            total = sum(estimate_tokens(p.markdown) for p in session.pages)
            context_label.set_text(f"{len(session.pages)} page(s), about {total} tokens" if session.pages else "")

    def add_log_entry(container, name: str, args: dict, text: str | None, ok: bool):
        icon = "hourglass_empty" if text is None else ("check" if ok else "error_outline")
        with container:
            entry = ui.expansion(describe_call(name, args), icon=icon).classes("w-full pc-tool")
            if text is not None:
                with entry:
                    ui.label(shorten(text)).classes("pc-tool-result")
        return entry

    def waiting_run() -> bool:
        return session.run is not None and session.run.resumable

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

    def render_history() -> None:
        turn_state["turn"] = None  # the chat is rebuilt, so any live bubble is gone
        chat_box.clear()
        with chat_box:
            if not session.history and not waiting_run():
                ui.label(
                    "Tell the agent what to scrape, or ask a question about the pages."
                    if agent_switch.value
                    else ("Ask a question about the scraped page." if session.pages else "Enter a URL above and choose Scrape. Then ask anything about the page.")
                ).classes("pc-empty")
            for message in session.history:
                if message.role == "user":
                    ui.chat_message(message.content, sent=True, name="You")
                    continue
                with ui.chat_message(name="Agent" if message.trace else "Qwen"):
                    for entry in message.trace:
                        add_log_entry(ui.column().classes("w-full gap-1 pc-log"), entry.name, entry.args, entry.text, entry.ok)
                    if message.reasoning:
                        with ui.expansion("Reasoning", icon="psychology").classes("w-full pc-reasoning"):
                            ui.markdown(message.reasoning)
                    ui.markdown(message.content)
                    if message.interrupted:
                        ui.label("Interrupted").classes("pc-interrupted")
                    if message.note:
                        ui.label(message.note).classes("pc-cite")
                    if message.sources:
                        ui.label("Based on " + ", ".join(message.sources)).classes("pc-cite")
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

    def refresh_results() -> None:
        results_box.clear()
        rows = session.rows
        results_box.set_visibility(bool(rows))
        if not rows:
            return
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with results_box:
            with ui.row().classes("w-full items-center"):
                ui.label(f"Results: {len(rows)} row(s)").classes("pc-pane-title")
                ui.button(
                    "Download CSV", icon="download", on_click=lambda: ui.download.content(rows_to_csv(session.rows).encode("utf-8"), "results.csv", "text/csv")
                ).props("flat dense").mark("download-csv")
                ui.button(
                    "Download JSON", icon="download", on_click=lambda: ui.download.content(rows_to_json(session.rows).encode("utf-8"), "results.json", "application/json")
                ).props("flat dense").mark("download-json")
            ui.table(
                columns=[{"name": c, "label": c, "field": c, "align": "left"} for c in columns],
                rows=[{"_i": i, **{c: table_cell(row.get(c)) for c in columns}} for i, row in enumerate(rows)],
                row_key="_i",
            ).classes("w-full pc-table").props("dense flat")

    # ---- scrape --------------------------------------------------------------
    def has_work_to_lose() -> bool:
        return bool(session.rows or session.history or waiting_run())

    async def do_scrape() -> None:
        if busy["scrape"] or busy["ask"]:
            return
        if has_work_to_lose():
            scrape_dialog.open()  # a manual scrape replaces the pages, results, chat and any saved run
            return
        await run_scrape()

    async def run_scrape() -> None:
        if busy["scrape"] or busy["ask"]:
            return
        raw = url_input.value
        busy["scrape"] = True
        notice_box.clear()
        update_controls()
        try:
            await session.scrape(raw, mode_select.value, follow=bool(follow.value), depth=int(depth.value or cfg.crawl_depth))
        except FetchError as exc:
            retry = None
            if exc.retry_mode:

                async def retry(mode=exc.retry_mode) -> None:
                    mode_select.set_value(mode)
                    await run_scrape()

            show_notice(exc.message, hint=exc.hint, action=retry, action_label=f"Retry with {exc.retry_mode}" if retry else "Retry")
            return
        except SessionBusy as exc:
            show_notice(str(exc), info=True)
            return
        finally:
            busy["scrape"] = False
            update_controls()
        render_history()
        refresh_sources()
        if session.pages:
            show_preview(session.pages[0])
            refresh_results()
            if session.pages[0].looks_empty and mode_select.value == "fast":

                async def use_dynamic() -> None:
                    mode_select.set_value("dynamic")
                    await run_scrape()

                show_notice(
                    "This page looks nearly empty.",
                    hint="It may be rendered with JavaScript.",
                    action=use_dynamic,
                    action_label="Retry with Dynamic",
                )
        update_controls()

    # ---- tool-free Q&A (Agent switch off) ------------------------------------
    async def run_ask(text: str) -> None:
        question.set_value("")
        notice_box.clear()
        busy["ask"] = True
        update_controls()
        if not session.history:
            chat_box.clear()  # drop the empty-state note
        with chat_box:
            ui.chat_message(text, sent=True, name="You")
            with ui.chat_message(name="Qwen"):
                with ui.expansion("Reasoning", icon="psychology").classes("w-full pc-reasoning") as think_box:
                    think_md = ui.markdown()
                answer_md = ui.markdown("Reading the page...").classes("pc-waiting")
        think_box.set_visibility(False)
        reasoning = answer = ""
        started = False
        error_text = None
        try:
            async for event in session.ask(text, model_select.value):
                if not started and event.kind != "error":
                    started = True
                    answer_md.classes(remove="pc-waiting")
                    answer_md.set_content("")
                if event.kind == "reasoning":
                    reasoning += event.text
                    think_box.set_visibility(True)
                    think_md.set_content(reasoning)
                elif event.kind == "answer":
                    answer += event.text
                    answer_md.set_content(answer)
                else:
                    error_text = event.text
                chat_scroll.scroll_to(percent=1.0)
        except Exception as exc:  # surface anything unexpected instead of leaving the UI locked
            error_text = f"Unexpected error: {exc}"
        finally:
            busy["ask"] = False
        interrupted = session.last_ask_interrupted
        render_history()
        refresh_sources()
        update_controls()
        if error_text:
            if not interrupted:
                question.set_value(text)
            show_notice(
                error_text,
                hint="The answer was cut off." if interrupted else None,
                action=regenerate if interrupted else None,
                action_label="Regenerate",
            )

    async def regenerate() -> None:
        if busy["ask"] or busy["scrape"]:
            return
        original = session.pop_interrupted()
        if original:
            render_history()
            await run_ask(original)

    # ---- the agent -----------------------------------------------------------
    turn_state: dict = {"turn": None}

    def show_pause(payload: dict) -> None:
        pause_box.clear()
        kind = payload.get("kind")
        with pause_box, ui.element("div").classes("pc-pause-card"):
            ui.label(payload.get("question", "The agent needs your input.")).classes("pc-pause-question")
            note = None
            if kind in ("ask", "limit"):
                hint = "Your answer" if kind == "ask" else "Add a note for the agent (optional)"
                note = ui.input(placeholder=hint).props("outlined dense").classes("w-full").mark("pause-note")
            with ui.row().classes("items-center"):
                if kind == "ask":
                    for option in payload.get("options", []):
                        ui.button(option, on_click=lambda o=option: answer(o)).mark(f"option:{option}")
                    ui.button("Send answer", on_click=lambda: answer((note.value or "").strip()) if (note.value or "").strip() else None).props("flat").mark("answer")
                elif kind == "limit":
                    for action in payload.get("actions", []):
                        ui.button(
                            action["label"],
                            on_click=lambda a=action["action"]: answer({"action": a, "note": (note.value or "").strip()}),
                        ).mark(f"action:{action['action']}")
                elif kind == "site":
                    ui.button("Allow", on_click=lambda: answer({"allow": True})).mark("allow")
                    ui.button("Deny", on_click=lambda: answer({"allow": False})).props("flat").mark("deny")
                ui.button("Discard run", on_click=discard).props("flat").mark("discard")
        update_controls()

    def new_turn() -> dict:
        with chat_box:
            with ui.chat_message(name="Agent") as bubble:
                step_label = ui.label("Working...").classes("pc-step")
                log_box = ui.column().classes("w-full gap-1 pc-log")
                with ui.expansion("Reasoning", icon="psychology").classes("w-full pc-reasoning") as think_box:
                    think_md = ui.markdown()
                answer_md = ui.markdown("Thinking...").classes("pc-waiting")
        think_box.set_visibility(False)
        return {"step": step_label, "log": log_box, "think_box": think_box, "think": think_md, "answer": answer_md, "entries": deque(), "waiting": True, "bubble": bubble, "fresh": False}

    async def consume(stream) -> dict:
        """Render one agent stream into an assistant bubble (a paused run keeps its bubble). Returns how it ended."""
        outcome = {"error": None, "limit": None, "paused": False}
        turn = turn_state["turn"] or new_turn()
        turn_state["turn"] = turn
        turn["step"].set_text("Working...")
        reasoning = answer = ""
        last_step = 0

        def stop_waiting() -> None:
            if turn["waiting"]:
                turn["waiting"] = False
                turn["answer"].classes(remove="pc-waiting")
                turn["answer"].set_content("")

        try:
            async with aclosing(stream) as events:
                async for event in events:
                    if event.kind != "followup":
                        turn["fresh"] = False
                    if event.kind == "step":
                        last_step = event.step
                        reasoning = answer = ""
                        turn["think"].set_content("")
                        turn["think_box"].set_visibility(False)
                        turn["answer"].set_content("Thinking...")
                        turn["answer"].classes(add="pc-waiting")
                        turn["waiting"] = True
                        turn["step"].set_text(f"Step {event.step}")
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
                    elif event.kind == "reasoning":
                        stop_waiting()
                        reasoning += event.text
                        turn["think_box"].set_visibility(True)
                        turn["think"].set_content(reasoning)
                    elif event.kind == "answer":
                        stop_waiting()
                        answer += event.text
                        turn["answer"].set_content(answer)
                    elif event.kind == "tool_start":
                        turn["entries"].append(add_log_entry(turn["log"], event.name, event.args, None, True))
                    elif event.kind == "tool_result":
                        if turn["entries"]:
                            entry = turn["entries"].popleft()
                            entry.props(f"icon={'check' if event.ok else 'error_outline'}")
                            with entry:
                                ui.label(shorten(event.text)).classes("pc-tool-result")
                        if event.urls:
                            refresh_sources()
                    elif event.kind == "rows_added":
                        refresh_results()
                    elif event.kind == "needs_input":
                        outcome["paused"] = True
                    elif event.kind == "limit":
                        outcome["limit"] = event.text
                    elif event.kind == "error":
                        outcome["error"] = event.text
                    chat_scroll.scroll_to(percent=1.0)
        except Exception as exc:  # surface anything unexpected instead of leaving the UI locked
            outcome["error"] = str(exc) if isinstance(exc, SessionBusy) else f"Unexpected error: {exc}"
        stop_waiting()
        if outcome["paused"]:
            turn["step"].set_text("Waiting for your answer")
        else:
            turn_state["turn"] = None
            turn["step"].set_text("Stopped" if outcome["limit"] == "stopped" else ("Interrupted" if outcome["error"] else "Finished"))
        return outcome

    async def run_stream(stream) -> None:
        busy["ask"] = busy["agent"] = True
        pause_box.clear()
        notice_box.clear()
        update_controls()
        before = len(session.history)
        outcome = await consume(stream)
        busy["ask"] = busy["agent"] = False
        restore_drafts(session.take_stopped_drafts())
        if len(session.history) > before:
            render_history()  # the run ended with an answer: rebuild with tool traces and sources
        refresh_sources()
        update_controls()
        refresh_results()
        if outcome["paused"]:
            show_pause(session.pending)
        elif outcome["error"]:
            show_notice(
                outcome["error"],
                hint="Your run is saved. Resume continues from the last step." if session.resumable else None,
                action=proceed if session.resumable else None,
                action_label="Resume",
            )
        elif outcome["limit"]:
            show_notice(
                LIMIT_MESSAGES.get(outcome["limit"], outcome["limit"]),
                info=True,
                hint="Resume continues from the last step." if session.resumable else None,
                action=proceed if session.resumable else None,
                action_label="Resume",
            )
        leftovers = [] if outcome["paused"] else session.take_leftovers()
        if leftovers:
            await start_agent("\n".join(leftovers))  # the run ended before it could read them: make them a normal turn

    async def start_agent(text: str) -> None:
        question.set_value("")
        queued_box.clear()  # bubbles of a run this instruction replaces; their text comes back as a draft
        queued.clear()
        turn_state["turn"] = None
        if not session.history:
            chat_box.clear()  # drop the empty-state note
        with chat_box:
            ui.chat_message(text, sent=True, name="You")
        await run_stream(session.run_agent(text, model_select.value, seed_url=(url_input.value or "").strip() or None))

    async def answer(value) -> None:
        if busy["ask"] or session.pending is None:
            return
        await run_stream(session.resume_run(value))

    async def proceed() -> None:
        if busy["ask"] or busy["scrape"] or not session.resumable:
            return
        await run_stream(session.proceed_run())

    async def discard() -> None:
        if busy["ask"]:
            return
        try:
            await session.discard_run()
        except SessionBusy as exc:
            show_notice(str(exc), info=True)
            return
        turn_state["turn"] = None
        pause_box.clear()
        notice_box.clear()
        render_history()
        restore_drafts(session.take_stopped_drafts())
        update_controls()

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

    # ---- models and status ---------------------------------------------------
    def on_model_change() -> None:
        info = models.get(model_select.value)
        if info is None:
            ctx_label.set_text("")
        elif info.loaded and info.context:
            ctx_label.set_text(f"context {info.context:,} tokens")
        else:
            ctx_label.set_text(f"not loaded: assuming {cfg.fallback_context:,} tokens. Load it in {session.llm.name}.")
        update_controls()

    def on_model_pick() -> None:
        if model_select.value in models and not syncing["models"]:
            session.remember_model(model_select.value)  # the user picked it: reopen with this model
        on_model_change()

    def sync_provider_chip() -> None:
        llm = session.llm
        hosted_chip.set_visibility(llm.hosted)
        hosted_label.set_text(f"Pages are sent to {llm.name}")
        hosted_tip.set_text(f"Page text is sent to {llm.name}'s servers when you ask a question or run the agent.")

    async def refresh_models() -> None:
        if busy["ask"]:
            return
        llm = session.llm
        try:
            infos = await llm.list_models()
        except LLMUnavailable as exc:
            if session.llm is not llm:  # the provider changed while we waited
                return
            models.clear()
            model_select.set_options({})
            status_dot.classes(replace="pc-dot pc-dot--bad")
            status_label.set_text(str(exc))
            update_controls()
            return
        if session.llm is not llm:
            return
        models.clear()
        models.update({m.id: m for m in infos})
        labels = {m.id: m.id if m.loaded else f"{m.id} (not loaded)" for m in infos}
        remembered = session.preferred_model
        keep = next((c for c in (model_select.value, remembered) if c in labels), infos[0].id if infos else None)
        syncing["models"] = True
        try:
            model_select.set_options(labels, value=keep)
        finally:
            syncing["models"] = False
        status_dot.classes(replace="pc-dot pc-dot--ok")
        status_label.set_text(f"{llm.name} connected")
        on_model_change()

    async def on_provider_change(event) -> None:
        key = event.value
        if key == session.provider:
            return
        try:
            if any(busy.values()):
                raise SessionBusy("Another run or scrape is already in progress. Wait for it to finish, or press Stop there.")
            await session.set_provider(key)
        except SessionBusy as exc:
            provider_select.set_value(session.provider)
            show_notice(str(exc), info=True)
            return
        sync_provider_chip()
        models.clear()
        model_select.set_options({}, value=None)
        status_dot.classes(replace="pc-dot pc-dot--bad")
        status_label.set_text(f"Connecting to {session.llm.name}...")
        ctx_label.set_text("")
        refresh_run_state()
        update_controls()
        await refresh_models()

    # ---- restore and new session ---------------------------------------------
    def refresh_run_state() -> None:
        """Show whichever card or banner the saved run needs under the active provider."""
        pause_box.clear()
        notice_box.clear()
        if session.pending is not None:
            show_pause(session.pending)
        elif session.resumable:
            show_resume_banner()
        elif session.foreign_run is not None:
            show_foreign_run_banner()

    def show_resume_banner() -> None:
        notice_box.clear()
        with notice_box, ui.element("div").classes("pc-notice pc-notice--info"):
            ui.label("A run was interrupted.").classes("pc-notice-title")
            ui.label(f"Instruction: {shorten(session.run.instruction, 160)}").classes("pc-notice-hint")
            with ui.row():
                ui.button("Resume", on_click=proceed).mark("resume")
                ui.button("Discard run", on_click=discard).props("flat").mark("discard")

    def show_foreign_run_banner() -> None:
        run = session.foreign_run
        notice_box.clear()
        with notice_box, ui.element("div").classes("pc-notice pc-notice--info"):
            ui.label(f"A run started on {run.provider} is saved.").classes("pc-notice-title")
            ui.label(f"Instruction: {shorten(run.instruction, 160)}").classes("pc-notice-hint")
            ui.label(f"It can only continue on {run.provider}. Discard it to carry on with {session.llm.name}.").classes("pc-notice-hint")
            ui.button("Discard run", on_click=discard).props("flat").mark("discard")

    async def confirm_new_session() -> None:
        dialog.close()
        if busy["ask"] or busy["scrape"]:
            return
        try:
            await session.new_session()
        except SessionBusy as exc:
            show_notice(str(exc), info=True)
            return
        url_input.set_value("")
        pause_box.clear()
        notice_box.clear()
        preview.set_content("Scraped text appears here, exactly as the model receives it.")
        refresh_results()
        render_history()
        refresh_sources()
        update_controls()

    with ui.dialog() as dialog, ui.card():
        ui.label("Start a new session? This clears the pages, results, chat and any paused run.")
        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat").mark("cancel-new")
            ui.button("Clear everything", on_click=confirm_new_session).mark("confirm-new")

    async def confirm_scrape() -> None:
        scrape_dialog.close()
        await run_scrape()

    with ui.dialog() as scrape_dialog, ui.card():
        ui.label("Scraping this page replaces the current pages, results, chat and any saved run. Continue?")
        with ui.row():
            ui.button("Cancel", on_click=scrape_dialog.close).props("flat").mark("cancel-scrape")
            ui.button("Replace", on_click=confirm_scrape).mark("confirm-scrape")

    # ---- wiring --------------------------------------------------------------
    scrape_btn.on_click(do_scrape)
    url_input.on("keydown.enter", do_scrape)
    cancel_btn.on_click(session.cancel)
    send_btn.on_click(send)
    stop_btn.on_click(session.cancel)
    new_btn.on_click(dialog.open)
    question.on("keydown.enter", send)
    url_input.on_value_change(lambda: session.set_setting("url", url_input.value or ""))
    mode_select.on_value_change(lambda: session.set_setting("mode", str(mode_select.value)))
    follow.on_value_change(lambda: session.set_setting("follow", "1" if follow.value else "0"))
    depth.on_value_change(lambda: depth.value and session.set_setting("depth", str(int(depth.value))))
    agent_switch.on_value_change(lambda: session.set_setting("agent", "1" if agent_switch.value else "0"))
    agent_switch.on_value_change(lambda: (update_controls(), render_history() if not session.history else None))
    model_select.on_value_change(on_model_pick)
    provider_select.on_value_change(on_provider_change)
    ui.timer(0.1, refresh_models, once=True)
    ui.timer(15.0, refresh_models)

    render_history()
    refresh_sources()
    if session.pages:
        show_preview(session.pages[0])
    update_controls()
    refresh_results()
    sync_provider_chip()
    refresh_run_state()
    if session.store is not None and session.store.recovered:
        show_notice(
            "The saved session could not be read, so a fresh one was started.",
            hint="The unreadable file was kept next to it with a .corrupt suffix.",
        )
