"""Routes that do work for the user: scrape a page, ask a question, run the agent. The long ones stream server-sent events."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from scraper.fetch import FetchError
from scraper.session import SessionBusy
from scraper.usage import UsageLimitReached

from .deps import Deps, get_deps, get_entry
from .errors import ApiError
from .events import error_frame, event_dict, frame
from .registry import Entry, TooBusy
from .schemas import AskBody, ResumeBody, RetryBody, RunBody, ScrapeBody, SteerBody, page_summary

log = logging.getLogger("scraper_api")
router = APIRouter()
STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _require_idle(entry: Entry) -> None:
    if entry.session.busy:
        raise ApiError(409, "busy", "Another run or scrape is already in progress. Wait for it to finish, or press Stop.")


def _require_room(deps: Deps, entry: Entry) -> None:
    """Refuse a new stream up front, with a proper status, when it could not run."""
    _require_idle(entry)
    if deps.slots.active >= deps.slots.limit:
        raise ApiError(429, "server_busy", "The server is busy with other runs. Try again in a minute.", headers={"Retry-After": "30"})
    spent = entry.meter.exhausted()
    if spent:
        raise ApiError(429, "limit", spent)


def stream_response(deps: Deps, entry: Entry, make_events: Callable[[], AsyncIterator]) -> StreamingResponse:
    """Run `make_events()` as a server-sent event stream. If the browser goes away the run is stopped and stays resumable."""

    async def body():
        session = entry.session
        owned = True  # false when this stream never got the session, so it must not touch the run that did
        try:
            with deps.slots.hold():
                async with aclosing(make_events()) as events:
                    async for event in events:
                        yield frame(event.kind, event_dict(event))
            yield frame(
                "end",
                {
                    "drafts": session.take_stopped_drafts(),
                    "leftovers": session.take_leftovers(),
                    "interrupted": session.last_ask_interrupted,
                    "usage": entry.meter.snapshot(),
                },
            )
        except TooBusy as exc:
            owned = False
            yield error_frame("server_busy", str(exc))
        except SessionBusy as exc:
            owned = False
            yield error_frame("busy", str(exc))
        except UsageLimitReached as exc:
            yield error_frame("limit", str(exc))
        except Exception as exc:  # never show the person a traceback, and never log text that may be page content
            log.error("stream failed: %s", type(exc).__name__)
            yield error_frame("server_error", "Something went wrong on the server. Your session is saved.")
        finally:
            if owned:
                session.release_after_disconnect()  # a no-op unless the browser left while the run was live

    return StreamingResponse(body(), media_type="text/event-stream", headers=STREAM_HEADERS)


# ---- the session -----------------------------------------------------------------------------


@router.post("/scrape")
async def scrape(body: ScrapeBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> dict:
    _require_idle(entry)
    try:
        with deps.slots.hold():
            result = await entry.session.scrape(body.url, body.mode, follow=body.follow, depth=body.depth)
    except TooBusy as exc:
        raise ApiError(429, "server_busy", str(exc), headers={"Retry-After": "30"}) from None
    except SessionBusy as exc:
        raise ApiError(409, "busy", str(exc)) from None
    except FetchError as exc:
        raise ApiError(400, "fetch_failed", exc.message, hint=exc.hint, retry_mode=exc.retry_mode) from None
    except UsageLimitReached as exc:
        raise ApiError(429, "limit", str(exc)) from None
    used: set[str] = set()
    return {
        "pages": [page_summary(p, used) for p in result.pages],
        "failed": [{"url": url, "reason": why} for url, why in result.failed],
    }


@router.post("/scrape/cancel", status_code=204)
async def scrape_cancel(entry: Entry = Depends(get_entry)) -> Response:
    entry.session.cancel()
    return Response(status_code=204)


@router.post("/session/new", status_code=204)
async def new_session(entry: Entry = Depends(get_entry)) -> Response:
    try:
        await entry.session.new_session()
    except SessionBusy as exc:
        raise ApiError(409, "busy", str(exc)) from None
    return Response(status_code=204)


# ---- streams -----------------------------------------------------------------------------------


@router.post("/ask")
async def ask(body: AskBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    _require_room(deps, entry)
    await deps.catalog.require(body.model)
    return stream_response(deps, entry, lambda: entry.session.ask(body.question, body.model))


@router.post("/ask/retry")
async def ask_retry(body: RetryBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    """Ask the last question again after an answer was cut off."""
    _require_room(deps, entry)
    await deps.catalog.require(body.model)
    question = entry.session.pop_interrupted()
    if question is None:
        raise ApiError(409, "nothing_to_retry", "There is no interrupted answer to retry.")
    return stream_response(deps, entry, lambda: entry.session.ask(question, body.model))


@router.post("/agent/run")
async def agent_run(body: RunBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    _require_room(deps, entry)
    await deps.catalog.require(body.model)
    return stream_response(deps, entry, lambda: entry.session.run_agent(body.instruction, body.model, seed_url=body.seed_url))


@router.post("/agent/resume")
async def agent_resume(body: ResumeBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    _require_room(deps, entry)
    if entry.session.run is None or entry.session.run.status != "paused":
        raise ApiError(409, "no_paused_run", "There is no question waiting for an answer.")
    return stream_response(deps, entry, lambda: entry.session.resume_run(body.value))


@router.post("/agent/proceed")
async def agent_proceed(entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    _require_room(deps, entry)
    if not entry.session.resumable:
        raise ApiError(409, "no_run", "There is no run to resume.")
    return stream_response(deps, entry, entry.session.proceed_run)


@router.post("/agent/stop", status_code=204)
async def agent_stop(entry: Entry = Depends(get_entry)) -> Response:
    entry.session.cancel()
    return Response(status_code=204)


@router.post("/agent/discard")
async def agent_discard(entry: Entry = Depends(get_entry)) -> dict:
    try:
        await entry.session.discard_run()
    except SessionBusy as exc:
        raise ApiError(409, "busy", str(exc)) from None
    return {"drafts": entry.session.take_stopped_drafts()}


@router.post("/agent/steer")
async def agent_steer(body: SteerBody, entry: Entry = Depends(get_entry)) -> dict:
    result = entry.session.steer(body.text)
    return {"status": result.status, "id": result.id}
