"""The spiders page's routes: what the pipeline can run, how its runs are going, the buttons that start, stop and retry them, and
each spider's settings. All of them are for administrators. They only read and write `pipeline` rows; a worker does the crawling."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body as JsonBody, Depends, Query
from pydantic import Field, model_validator

from scraper.pipeline.domain import FetchSummary, Run
from scraper.pipeline.repositories import SourceBusy
from scraper.pipeline.settings import (
    LocationProblem, SettingsInvalid, SpiderSettings, describe_form, item_fields, load_settings, location_options, run_options,
    settings_defaults,
)
from scraper.pipeline.spider import SourceSpider
from scraper.pipeline.uow import UnitOfWork

from .admins import AdminCheckUnavailable, is_admin
from .deps import Deps, PipelineAccess, current_user, get_deps
from .errors import ApiError
from .schemas import Body

router = APIRouter(prefix="/pipeline")
HISTORY_DEFAULT, HISTORY_MAX = 30, 100
FETCH_PAGE = 200
TRIAL_MAX = 20
HEAVY_STATS = ("trial_items", "rejected_samples")  # only the run's own view carries these
RETRYABLE = ("failed", "paused")


def runnable_spiders() -> dict[str, type[SourceSpider]]:
    """The registered spiders that run through the pipeline. (A spider that is not a `SourceSpider` has nowhere to store anything.)"""
    from scraper.pipeline.sources import SPIDERS

    return {name: cls for name, cls in SPIDERS.items() if isinstance(cls, type) and issubclass(cls, SourceSpider)}


def require_admin(user_id: str = Depends(current_user), deps: Deps = Depends(get_deps)) -> str:
    try:
        allowed = is_admin(deps.pool, user_id)
    except AdminCheckUnavailable:
        raise ApiError(503, "admin_unavailable", "Administrator checks are unavailable right now. Try again shortly.") from None
    if not allowed:
        raise ApiError(403, "forbidden", "Only administrators can use this.")
    return user_id


def pipeline_of(deps: Deps = Depends(get_deps)) -> PipelineAccess:
    if deps.pipeline is None:
        raise ApiError(503, "pipeline_unavailable", "The scraping pipeline is not set up on this server.")
    return deps.pipeline


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _location_of(spider_cls: type[SourceSpider] | None, run: Run) -> dict | None:
    """The run's location as `{id, label}`, read with the spider's own field names, so the page never needs to know them."""
    entry = run.options.get("location")
    if not isinstance(entry, dict) or spider_cls is None or not spider_cls.locations_setting:
        return None
    return {"id": entry.get(spider_cls.location_id_field), "label": entry.get(spider_cls.location_label_field)}


def run_view(run: Run, source: str, spider_cls: type[SourceSpider] | None = None, *, detail: bool = False) -> dict:
    stats = run.stats if detail else {key: value for key, value in run.stats.items() if key not in HEAVY_STATS}
    return {
        "id": run.id, "source": source, "kind": run.kind, "status": run.status, "queued_at": _iso(run.queued_at),
        "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at), "heartbeat_at": _iso(run.heartbeat_at),
        "retry_of": run.retry_of, "cancel_requested": run.cancel_requested, "options": run.options, "stats": stats, "error": run.error,
        "location": _location_of(spider_cls, run),
    }


def fetch_view(fetch: FetchSummary) -> dict:
    return {
        "id": fetch.id, "url": fetch.url, "final_url": fetch.final_url, "status": fetch.status,
        "fetched_at": _iso(fetch.fetched_at), "duration_ms": fetch.duration_ms, "size": fetch.size,
    }


def _busy() -> ApiError:
    return ApiError(409, "source_busy", "A run is already queued or running for this spider.")


def _no_run() -> ApiError:
    return ApiError(404, "not_found", "There is no such run.")


def _known(access: PipelineAccess, name: str) -> type[SourceSpider]:
    if name not in access.spiders:
        raise ApiError(404, "not_found", "There is no such spider.")
    return access.spiders[name]


def _summary(spider_cls: type[SourceSpider], raw: dict) -> dict:
    """What the list page needs from a spider's settings. Settings that do not validate offer no locations; the settings screen says why."""
    try:
        settings = load_settings(spider_cls, raw)
    except SettingsInvalid:
        return {"locations": [], "trial_max_requests": SpiderSettings().trial_max_requests}
    return {"locations": location_options(spider_cls, settings), "trial_max_requests": settings.trial_max_requests}


def settings_view(spider_cls: type[SourceSpider], raw: dict) -> dict:
    """The saved settings with defaults filled in. If what is stored no longer validates, the stored values come back as they are,
    with `problems` saying why, so the form can show them and the administrator can fix them."""
    problems: list[str] = []
    try:
        settings: dict = load_settings(spider_cls, raw).model_dump(mode="json")
    except SettingsInvalid as error:
        settings, problems = raw, [error.message]
    return {
        "settings": settings, "defaults": settings_defaults(spider_cls), "problems": problems, "form": describe_form(spider_cls),
        "fields": item_fields(spider_cls), "locations_setting": spider_cls.locations_setting,
        "location_id_field": spider_cls.location_id_field, "location_label_field": spider_cls.location_label_field,
    }


def _options_for(
    spider_cls: type[SourceSpider], raw: dict, *, kind: str, location_id: str | None, max_requests: int | None, location_status: int = 422
) -> dict:
    """The snapshot a run is queued with, or the reason it cannot be."""
    try:
        settings = load_settings(spider_cls, raw)
    except SettingsInvalid as error:
        raise ApiError(422, "invalid_settings", f"The saved settings are not valid ({error.message}). Fix them in Settings.") from None
    try:
        return run_options(spider_cls, settings, location_id=location_id, kind=kind, max_requests=max_requests)
    except LocationProblem as error:
        raise ApiError(location_status, error.code, error.message) from None


class StartBody(Body):
    kind: Literal["live", "trial"] = "live"
    max_requests: int | None = Field(default=None, ge=1, le=TRIAL_MAX)
    location: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _only_trials_have_a_cap(self):
        if self.kind == "live" and self.max_requests is not None:
            raise ValueError("max_requests applies to trial runs only")
        return self


@router.get("/sources")
def sources(access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        known = {source.name: source for source in uow.sources.all()}
        for name in access.spiders:
            if name not in known:
                known[name] = uow.sources.ensure(name)
        latest = uow.runs.latest_by_source()
        listed = [
            {
                "name": name,
                **_summary(access.spiders[name], known[name].settings),
                "latest_run": run_view(latest[known[name].id], name, access.spiders[name]) if known[name].id in latest else None,
            }
            for name in sorted(access.spiders)
        ]
    return {"sources": listed}


@router.get("/sources/{name}/settings")
def get_settings(name: str, access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    spider_cls = _known(access, name)
    with UnitOfWork(access.engine, access.blobs) as uow:
        raw = uow.sources.ensure(name).settings
    return settings_view(spider_cls, raw)


@router.put("/sources/{name}/settings")
def put_settings(
    name: str, body: dict[str, Any] = JsonBody(...), access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)
) -> dict:
    spider_cls = _known(access, name)
    try:
        settings = load_settings(spider_cls, body)
    except SettingsInvalid as error:
        raise ApiError(422, "invalid_settings", error.message) from None
    saved = settings.model_dump(mode="json")
    with UnitOfWork(access.engine, access.blobs) as uow:
        uow.sources.set_settings(uow.sources.ensure(name).id, saved)
    return settings_view(spider_cls, saved)


@router.get("/sources/{name}/runs")
def history(
    name: str, limit: int = Query(HISTORY_DEFAULT, ge=1, le=HISTORY_MAX), access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)
) -> dict:
    spider_cls = _known(access, name)
    with UnitOfWork(access.engine, access.blobs) as uow:
        source = uow.sources.get(name)
        runs = uow.runs.recent(source.id, limit) if source else []
    return {"runs": [run_view(run, name, spider_cls) for run in runs]}


@router.post("/sources/{name}/runs", status_code=201)
def start(name: str, body: StartBody, access: PipelineAccess = Depends(pipeline_of), user_id: str = Depends(require_admin)) -> dict:
    spider_cls = _known(access, name)
    try:
        with UnitOfWork(access.engine, access.blobs) as uow:
            source = uow.sources.ensure(name)
            options = _options_for(spider_cls, source.settings, kind=body.kind, location_id=body.location, max_requests=body.max_requests)
            run = uow.runs.enqueue(source.id, kind=body.kind, requested_by=user_id, options=options)
    except SourceBusy:
        raise _busy() from None
    return run_view(run, name, spider_cls)


@router.get("/runs/{run_id}")
def one_run(run_id: int, access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        run = uow.runs.get(run_id)
        source = uow.sources.by_id(run.source_id) if run else None
    if run is None or source is None:
        raise _no_run()
    return run_view(run, source.name, access.spiders.get(source.name), detail=True)


@router.get("/runs/{run_id}/fetches")
def fetches(run_id: int, after: int = Query(0, ge=0), access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        if uow.runs.get(run_id) is None:
            raise _no_run()
        rows = uow.fetches.since(run_id, after, FETCH_PAGE)
    return {"fetches": [fetch_view(row) for row in rows]}


@router.post("/runs/{run_id}/stop", status_code=202)
def stop(run_id: int, access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        before = uow.runs.get(run_id)
        if before is None:
            raise _no_run()
        run = uow.runs.request_cancel(run_id)
        source = uow.sources.by_id(before.source_id)
    if run is None:
        raise ApiError(409, "not_active", "This run has already finished.")
    return run_view(run, source.name, access.spiders.get(source.name))


@router.post("/runs/{run_id}/retry", status_code=201)
def retry(run_id: int, access: PipelineAccess = Depends(pipeline_of), user_id: str = Depends(require_admin)) -> dict:
    try:
        with UnitOfWork(access.engine, access.blobs) as uow:
            old = uow.runs.get(run_id)
            source = uow.sources.by_id(old.source_id) if old else None
            if old is None or source is None:
                raise _no_run()
            if old.status not in RETRYABLE:
                raise ApiError(409, "not_retryable", "Only a failed or stopped run can be retried.")
            spider_cls = _known(access, source.name)
            entry = old.options.get("location")
            location_id = entry.get(spider_cls.location_id_field) if isinstance(entry, dict) else None
            if spider_cls.locations_setting and location_id is None:
                raise ApiError(409, "unknown_location", "That run did not record a location. Start a new run and pick one.")
            options = _options_for(
                spider_cls, source.settings, kind=old.kind, location_id=location_id, max_requests=old.options.get("max_requests"), location_status=409
            )
            run = uow.runs.enqueue(old.source_id, kind=old.kind, retry_of=old.id, requested_by=user_id, options=options)
    except SourceBusy:
        raise _busy() from None
    return run_view(run, source.name, spider_cls)
