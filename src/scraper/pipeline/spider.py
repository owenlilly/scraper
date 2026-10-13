"""SourceSpider: Scrapling's Spider with production defaults, an archive of every response, and the item pipeline.

A source author writes `parse` and declares the item schema; this class does the rest. Spiders contain no storage code:
everything is written through repositories, in worker threads so the crawl's event loop never waits on the database."""

import asyncio
import threading
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError
from scrapling.spiders import Spider
from sqlalchemy import Engine

from .blobs import FilesystemBlobStore
from .domain import RecordItem, UpsertResult
from .items import prepare
from .uow import UnitOfWork

BATCH_SIZE = 50
SAMPLE_LIMIT = 5

# Scrapling runs each request in its own task and calls `is_blocked` and then the callback (and so `on_scraped_item`) in that
# task, so a value set in the first is seen by the others for the same request and by no other request.
_current_fetch: ContextVar[int | None] = ContextVar("pipeline_fetch_id", default=None)


TRIAL_ITEM_LIMIT = 25
TRIAL_DEFAULT_REQUESTS = 5
TRIAL_VALUE_CHARS = 500


@dataclass(frozen=True)
class RunContext:
    engine: Engine
    blobs: FilesystemBlobStore
    source_id: int
    run_id: int
    kind: str = "live"  # "trial" crawls a few pages and stores no records
    options: dict = field(default_factory=dict)


def _clipped(value):
    """A trial's items are shown on a page and kept in `run.stats`, so one huge field must not bloat either."""
    if isinstance(value, str):
        return value if len(value) <= TRIAL_VALUE_CHARS else value[:TRIAL_VALUE_CHARS] + "…"
    if isinstance(value, dict):
        return {key: _clipped(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clipped(item) for item in value]
    return value


class SourceSpider(Spider):
    """Subclass and set `name`, `start_urls`, `allowed_domains`, `item_schema`, `natural_key` and `parser_version`, then
    write `async def parse(self, response)` as for any Scrapling spider: yield dicts (items) and requests."""

    item_schema: type[BaseModel]
    natural_key: tuple[str, ...]
    parser_version: str

    robots_txt_obey = True
    autothrottle_enabled = True

    def __init__(self, *, ctx: RunContext, crawldir=None, interval: float = 300.0) -> None:
        super().__init__(crawldir=crawldir, interval=interval)
        self.ctx = ctx
        self._buffer: list[RecordItem] = []
        self._written = UpsertResult()
        self._rejected = 0
        self._unarchived = 0
        self._rejected_fields: Counter[str] = Counter()
        self._rejected_samples: list[dict] = []
        self.write_error: str | None = None
        self.trial = ctx.kind == "trial"
        self.trial_requests = int(ctx.options.get("max_requests", TRIAL_DEFAULT_REQUESTS))
        self.stopped_at_cap = False
        self._trial_items: list[dict] = []
        self._stop_lock = threading.Lock()
        self._stop_asked = False
        self._last_snapshot: dict = {}

    # ---- the archive ------------------------------------------------------------------------------------------------------

    async def is_blocked(self, response) -> bool:
        """Scrapling calls this for every live response, blocked or not, so it is where every response is archived. Blocked
        pages are kept too: they are the evidence when a site changes its defences."""
        try:
            fetch_id = await asyncio.to_thread(self._archive, response)
        except Exception as error:
            self._fail(f"archiving {response.url}: {error!r}")
            return False
        _current_fetch.set(fetch_id)
        self._stop_at_trial_cap()
        return await self.looks_blocked(response)

    async def looks_blocked(self, response) -> bool:
        """Override for a site that answers 200 with a challenge page. The default is Scrapling's: a status in its blocked set."""
        return await super().is_blocked(response)

    def _stop_at_trial_cap(self) -> None:
        if self.trial and not self.stopped_at_cap and self.stats.requests_count >= self.trial_requests:
            self.stopped_at_cap = True
            self.stop_once()

    def stop_once(self) -> bool:
        """Ask the crawl to finish what is in flight and stop. Scrapling treats a second request as a force stop, so only the
        first is passed on. False when no crawl is active yet (Scrapling refuses), so the caller may try again."""
        with self._stop_lock:
            if self._stop_asked:
                return True
            try:
                self.pause()
            except RuntimeError:
                return False
            self._stop_asked = True
            return True

    def snapshot(self) -> dict:
        """The numbers so far, for a heartbeat thread. A counter may change while it is copied; then the last good copy stands."""
        try:
            merged = {**self.stats.to_dict(), **self.pipeline_stats()}
        except RuntimeError:  # no crawl is active, or a counter changed mid-copy
            return self._last_snapshot
        self._last_snapshot = merged
        return merged

    def _archive(self, response) -> int:
        requested = response.request.url if getattr(response, "request", None) else response.url
        with UnitOfWork(self.ctx.engine, self.ctx.blobs) as uow:
            ref = uow.fetches.save(
                self.ctx.run_id, url=requested, final_url=response.url, status=response.status,
                headers={str(k): str(v) for k, v in response.headers.items()}, body=response.body, fetcher="scrapling",
            )
        return ref.id

    # ---- the item pipeline ------------------------------------------------------------------------------------------------

    async def on_scraped_item(self, item: dict) -> dict | None:
        fetch_id = _current_fetch.get()
        if fetch_id is None and not self.development_mode:
            # The page this came from was not archived (storage failed), so the record could not be traced back to it. Scrapling's
            # development cache replays pages without calling `is_blocked`, which is the one case where there is no fetch to point at.
            self._unarchived += 1
            return None
        try:
            record = prepare(self.item_schema, self.natural_key, item, fetch_id)
        except ValidationError as error:
            self._reject(item, error)
            return None
        if self.trial:
            if len(self._trial_items) < TRIAL_ITEM_LIMIT:
                self._trial_items.append(_clipped(record.data))
            return item
        self._buffer.append(record)
        if len(self._buffer) >= BATCH_SIZE:
            await self._flush()
        return item

    def _reject(self, item: dict, error: ValidationError) -> None:
        problems = error.errors(include_url=False, include_context=False, include_input=False)
        self._rejected += 1
        self._rejected_fields.update(str(problem["loc"][0]) if problem["loc"] else "?" for problem in problems)
        if len(self._rejected_samples) < SAMPLE_LIMIT:
            sample = {key: str(value)[:200].replace("\x00", "") for key, value in item.items()}
            self._rejected_samples.append({"item": sample, "errors": [f"{'.'.join(map(str, p['loc']))}: {p['msg']}" for p in problems]})

    async def _flush(self) -> None:
        batch, self._buffer = self._buffer, []
        if not batch:
            return
        try:
            self._written += await asyncio.to_thread(self._write, batch)
        except Exception as error:
            self._fail(f"writing {len(batch)} records: {error!r}")

    def _write(self, batch: list[RecordItem]) -> UpsertResult:
        with UnitOfWork(self.ctx.engine, self.ctx.blobs) as uow:
            return uow.records.upsert_batch(self.ctx.source_id, self.ctx.run_id, self.parser_version, batch)

    def _fail(self, message: str) -> None:
        """Storage is broken, so carrying on would only scrape pages that cannot be saved. Stop gracefully; the runner marks
        the run failed."""
        if self.write_error is None:
            self.write_error = message
            self.stop_once()

    async def on_close(self) -> None:
        await self._flush()

    def pipeline_stats(self) -> dict:
        stats = {
            "records_created": self._written.created,
            "records_changed": self._written.changed,
            "records_unchanged": self._written.unchanged,
            "items_rejected": self._rejected,
            "items_unarchived": self._unarchived,
            "rejected_by_field": dict(self._rejected_fields),
            "rejected_samples": list(self._rejected_samples),
        }
        if self.trial:
            stats["trial_items"] = list(self._trial_items)
            stats["stopped_at_cap"] = self.stopped_at_cap
        return stats
