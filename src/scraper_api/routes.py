"""Routes that read or change a user's saved state. All but /health and /account-data act on the signed-in user's own session."""

import asyncio
import hmac
import time

from fastapi import APIRouter, Depends, Header, Response

from scraper.exports import rows_to_csv, rows_to_json

from .accounts import delete_user_data
from .deps import Deps, get_deps, get_entry
from .errors import ApiError
from .registry import Entry
from .schemas import SETTING_NAMES, DeleteAccountBody, ModelBody, SettingBody, build_state, model_dict

router = APIRouter()
DELETE_WAIT_SECONDS = 5.0  # how long a deletion waits for the person's live run to stop
DELETE_SETTLE_SECONDS = 0.25  # trailing writes from a stopped run (checkpoints are saved in the background)


# ---- open ------------------------------------------------------------------------------


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.delete("/account-data", status_code=204)
async def account_data(
    body: DeleteAccountBody, deps: Deps = Depends(get_deps), x_service_secret: str = Header(default="")
) -> Response:
    """Called by the web app when it deletes an account. Authenticated by a shared secret, not by a user token."""
    if not x_service_secret or not hmac.compare_digest(x_service_secret.encode(), deps.cfg.service_secret.encode()):
        raise ApiError(401, "unauthorized", "Not allowed.")
    entry = deps.registry.peek(body.user_id)
    if entry is not None:
        entry.session.store.close()  # from now on a run that is still stopping cannot write anything back
        entry.session.cancel()
        deadline = time.monotonic() + DELETE_WAIT_SECONDS
        while entry.session.busy and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if entry.session.busy:
            raise ApiError(409, "busy", "A run is still stopping. Try again in a few seconds.")
    deps.registry.forget(body.user_id)
    delete_user_data(deps.pool, body.user_id)
    if entry is not None:
        await asyncio.sleep(DELETE_SETTLE_SECONDS)
        delete_user_data(deps.pool, body.user_id)  # and again, for what the stopped run saved in its last moments
    return Response(status_code=204)


# ---- state ------------------------------------------------------------------------------


@router.get("/state")
async def state(entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> dict:
    models, error = await deps.catalog.safe_models()
    return build_state(entry, models, error)


@router.get("/models")
async def models(entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> dict:
    listed, error = await deps.catalog.safe_models()
    return {"models": [model_dict(m) for m in listed], "error": error}


@router.get("/pages/{index}")
async def page(index: int, entry: Entry = Depends(get_entry)) -> dict:
    pages = entry.session.pages
    if not 0 <= index < len(pages):
        raise ApiError(404, "not_found", "There is no such page.")
    p = pages[index]
    return {"url": p.url, "title": p.title, "markdown": p.markdown}


@router.get("/context")
async def context(entry: Entry = Depends(get_entry)) -> dict:
    last = entry.session.last_context
    if last is None:
        return {"text": "", "sources": [], "mode": None}
    return {"text": last.text, "sources": list(last.sources), "mode": last.mode}


@router.put("/settings/{name}", status_code=204)
async def setting(name: str, body: SettingBody, entry: Entry = Depends(get_entry)) -> Response:
    if name not in SETTING_NAMES:
        raise ApiError(404, "not_found", "There is no such setting.")
    entry.session.set_setting(name, body.value)
    return Response(status_code=204)


@router.put("/model", status_code=204)
async def model(body: ModelBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> Response:
    await deps.catalog.require(body.id)
    entry.session.remember_model(body.id)
    return Response(status_code=204)


# ---- results -----------------------------------------------------------------------------------


@router.get("/rows.csv")
async def rows_csv(entry: Entry = Depends(get_entry)) -> Response:
    return Response(
        rows_to_csv(entry.session.rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="results.csv"', "X-Content-Type-Options": "nosniff"},
    )


@router.get("/rows.json")
async def rows_json(entry: Entry = Depends(get_entry)) -> Response:
    return Response(
        rows_to_json(entry.session.rows),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="results.json"'},
    )
