"""NiceGUI front end. Renders Session state and forwards user actions; no business logic."""

from nicegui import ui

from .config import Config
from .context import estimate_tokens
from .fetch import FetchError, Page
from .llm import LLMUnavailable, ModelInfo
from .session import Session

MODE_LABELS = {
    "fast": "Fast (HTTP)",
    "dynamic": "Dynamic (browser)",
    "stealthy": "Stealthy (anti-bot)",
}


def build_ui(cfg: Config, llm, session: Session) -> None:
    ui.dark_mode(True)
    models: dict[str, ModelInfo] = {}
    busy = {"scrape": False, "ask": False}

    # ---- top bar -------------------------------------------------------------
    with ui.header(elevated=False).classes("items-center gap-3 px-4 py-2"):
        ui.label("Page Chat").classes("text-lg font-semibold")
        url_input = (
            ui.input(placeholder="https://example.com")
            .props("dense outlined clearable")
            .classes("grow")
            .mark("url")
        )
        mode_select = ui.select(MODE_LABELS, value="fast").props("dense outlined")
        follow = ui.checkbox("Follow links")
        depth = ui.number("Depth", value=cfg.crawl_depth, min=1, max=3, precision=0).props("dense outlined").classes("w-20")
        depth.bind_visibility_from(follow, "value")
        scrape_btn = ui.button("Scrape", icon="travel_explore").mark("scrape")
        cancel_btn = ui.button("Cancel", icon="close").props("flat").mark("cancel")
        cancel_btn.set_visibility(False)

    progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
    progress.set_visibility(False)
    notice_box = ui.column().classes("w-full px-4")

    # ---- body ----------------------------------------------------------------
    with ui.splitter(value=34).classes("w-full grow") as split:
        with split.before:
            ui.label("Sources").classes("text-overline px-2")
            sources_box = ui.column().classes("w-full gap-0")
            context_label = ui.label("").classes("text-xs opacity-70 px-2")
            ui.separator()
            ui.label("Preview").classes("text-overline px-2")
            with ui.scroll_area().classes("w-full h-96 px-2"):
                preview = ui.markdown("Select a source to see the text the model receives.")
        with split.after:
            with ui.scroll_area().classes("w-full h-[60vh]") as chat_scroll:
                chat_box = ui.column().classes("w-full gap-2 px-2")
            with ui.row().classes("w-full items-center px-2"):
                question = ui.input(placeholder="Ask about this site...").props("outlined dense").classes("grow").mark("question")
                send_btn = ui.button(icon="send").mark("send")

    # ---- footer --------------------------------------------------------------
    with ui.footer().classes("items-center gap-3 px-4 py-1"):
        status_dot = ui.icon("circle", size="xs").classes("text-red-500")
        status_label = ui.label("Connecting to LM Studio...").classes("text-xs")
        model_select = ui.select({}, label="Model").props("dense borderless").classes("min-w-64").mark("model")
        ctx_label = ui.label("").classes("text-xs opacity-70")

    # ---- helpers -------------------------------------------------------------
    def update_controls() -> None:
        scraping, asking = busy["scrape"], busy["ask"]
        scrape_btn.set_enabled(not scraping and not asking)
        cancel_btn.set_visibility(scraping and bool(follow.value))
        progress.set_visibility(scraping)
        ticker.active = scraping
        send_btn.set_enabled(bool(session.pages) and bool(model_select.value) and not scraping and not asking)
        question.set_enabled(not asking)

    def update_progress() -> None:
        done, total = session.progress
        progress.set_value(done / total if total else 0)

    ticker = ui.timer(0.25, update_progress, active=False)

    def show_notice(message: str, *, hint: str | None = None, action=None, action_label: str = "Retry") -> None:
        notice_box.clear()
        with notice_box, ui.card().classes("w-full"):
            ui.label(message).classes("font-medium")
            if hint:
                ui.label(hint).classes("text-sm opacity-80")
            with ui.row():
                if action:
                    ui.button(action_label, on_click=action).props("flat")
                ui.button("Dismiss", on_click=notice_box.clear).props("flat")

    def show_preview(page: Page) -> None:
        preview.set_content(f"**{page.title}**\n\n{page.url}\n\n---\n\n{page.markdown}")

    def refresh_sources() -> None:
        used = set(session.last_context.sources) if session.last_context else set()
        sources_box.clear()
        with sources_box:
            for page in session.pages:
                with ui.item(on_click=lambda p=page: show_preview(p)).classes("w-full"):
                    with ui.item_section().props("avatar"):
                        ui.icon("circle", size="xs").classes("text-green-400" if page.url in used else "opacity-40")
                    with ui.item_section():
                        ui.item_label(page.title)
                        ui.item_label(f"{page.url} - about {estimate_tokens(page.markdown)} tokens").props("caption")
            for url, why in session.failed:
                ui.label(f"Failed: {url} ({why})").classes("text-xs text-red-400 px-2")
        if session.last_context:
            context_label.set_text(
                f"Last answer used {session.last_context.mode} context from {len(session.last_context.sources)} page(s)"
            )
        else:
            total = sum(estimate_tokens(p.markdown) for p in session.pages)
            context_label.set_text(f"{len(session.pages)} page(s), about {total} tokens" if session.pages else "")

    def render_history() -> None:
        chat_box.clear()
        with chat_box:
            for message in session.history:
                if message.role == "user":
                    ui.chat_message(message.content, sent=True, name="You")
                    continue
                with ui.chat_message(name="Qwen"):
                    if message.reasoning:
                        with ui.expansion("Reasoning", icon="psychology").classes("w-full"):
                            ui.markdown(message.reasoning)
                    ui.markdown(message.content)
                    if message.interrupted:
                        ui.label("Interrupted").classes("text-xs text-orange-400")
                    if message.sources:
                        ui.label("Sources: " + ", ".join(message.sources)).classes("text-xs opacity-60")
        chat_scroll.scroll_to(percent=1.0)

    # ---- scrape --------------------------------------------------------------
    async def do_scrape() -> None:
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
                    await do_scrape()

            show_notice(exc.message, hint=exc.hint, action=retry, action_label=f"Retry with {exc.retry_mode}" if retry else "Retry")
            return
        finally:
            busy["scrape"] = False
            update_controls()
        render_history()
        refresh_sources()
        if session.pages:
            show_preview(session.pages[0])
            if session.pages[0].looks_empty and mode_select.value == "fast":

                async def use_dynamic() -> None:
                    mode_select.set_value("dynamic")
                    await do_scrape()

                show_notice(
                    "This page looks nearly empty.",
                    hint="It may be rendered with JavaScript.",
                    action=use_dynamic,
                    action_label="Retry with Dynamic",
                )
        update_controls()

    # ---- ask -----------------------------------------------------------------
    async def run_ask(text: str) -> None:
        question.set_value("")
        notice_box.clear()
        busy["ask"] = True
        update_controls()
        with chat_box:
            ui.chat_message(text, sent=True, name="You")
            with ui.chat_message(name="Qwen"):
                with ui.expansion("Reasoning", icon="psychology").classes("w-full") as think_box:
                    think_md = ui.markdown()
                answer_md = ui.markdown()
        think_box.set_visibility(False)
        reasoning = answer = ""
        error_text = None
        try:
            async for event in session.ask(text, model_select.value):
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
        finally:
            busy["ask"] = False
        interrupted = bool(session.history) and session.history[-1].interrupted
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
        original = session.pop_interrupted()
        if original:
            render_history()
            await run_ask(original)

    async def send() -> None:
        text = (question.value or "").strip()
        if not text or busy["ask"] or busy["scrape"] or not session.pages or not model_select.value:
            return
        await run_ask(text)

    # ---- models and status ---------------------------------------------------
    def on_model_change() -> None:
        info = models.get(model_select.value)
        if info is None:
            ctx_label.set_text("")
        elif info.loaded and info.context:
            ctx_label.set_text(f"context {info.context:,} tokens")
        else:
            ctx_label.set_text(f"not loaded: assuming {cfg.fallback_context:,} tokens. Load it in LM Studio.")
        update_controls()

    async def refresh_models() -> None:
        if busy["ask"]:
            return
        try:
            infos = await llm.list_models()
        except LLMUnavailable as exc:
            models.clear()
            model_select.set_options({})
            status_dot.classes(replace="text-red-500")
            status_label.set_text(str(exc))
            update_controls()
            return
        models.clear()
        models.update({m.id: m for m in infos})
        labels = {m.id: m.id if m.loaded else f"{m.id} (not loaded)" for m in infos}
        keep = model_select.value if model_select.value in labels else (infos[0].id if infos else None)
        model_select.set_options(labels, value=keep)
        status_dot.classes(replace="text-green-500")
        status_label.set_text("LM Studio connected")
        on_model_change()

    # ---- wiring --------------------------------------------------------------
    scrape_btn.on_click(do_scrape)
    url_input.on("keydown.enter", do_scrape)
    cancel_btn.on_click(session.cancel)
    send_btn.on_click(send)
    question.on("keydown.enter", send)
    model_select.on_value_change(on_model_change)
    ui.timer(0.1, refresh_models, once=True)
    ui.timer(15.0, refresh_models)

    render_history()
    refresh_sources()
    update_controls()
