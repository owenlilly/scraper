"""SourceSpider: Scrapling's Spider with production defaults, an archive of every response, and the item pipeline.

A source author writes `parse` and declares the item schema; this class does the rest. Spiders contain no storage code:
everything is written through repositories, in worker threads so the crawl's event loop never waits on the database."""

import asyncio
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass

from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ValidationError
from scrapling.spiders import Spider

from .blobs import FilesystemBlobStore
from .domain import RecordItem, UpsertResult
from .items import prepare
from .uow import UnitOfWork

BATCH_SIZE = 50
SAMPLE_LIMIT = 5

# Scrapling runs each request in its own task and calls `is_blocked` and then the callback (and so `on_scraped_item`) in that
# task, so a value set in the first is seen by the others for the same request and by no other request.
_current_fetch: ContextVar[int | None] = ContextVar("pipeline_fetch_id", default=None)


@dataclass(frozen=True)
class RunContext:
    pool: ConnectionPool
    blobs: FilesystemBlobStore
    source_id: int
    run_id: int


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
        return await self.looks_blocked(response)

    async def looks_blocked(self, response) -> bool:
        """Override for a site that answers 200 with a challenge page. The default is Scrapling's: a status in its blocked set."""
        return await super().is_blocked(response)

    def _archive(self, response) -> int:
        requested = response.request.url if getattr(response, "request", None) else response.url
        with UnitOfWork(self.ctx.pool, self.ctx.blobs) as uow:
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
        self._buffer.append(record)
        if len(self._buffer) >= BATCH_SIZE:
            await self._flush()
        return item

    def _reject(self, item: dict, error: ValidationError) -> None:
        problems = error.errors(include_url=False, include_context=False, include_input=False)
        self._rejected += 1
        self._rejected_fields.update(str(problem["loc"][0]) if problem["loc"] else "?" for problem in problems)
        if len(self._rejected_samples) < SAMPLE_LIMIT:
            sample = {key: str(value)[:200] for key, value in item.items()}
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
        with UnitOfWork(self.ctx.pool, self.ctx.blobs) as uow:
            return uow.records.upsert_batch(self.ctx.source_id, self.ctx.run_id, self.parser_version, batch)

    def _fail(self, message: str) -> None:
        """Storage is broken, so carrying on would only scrape pages that cannot be saved. Stop gracefully; the runner marks
        the run failed."""
        if self.write_error is None:
            self.write_error = message
            self.pause()

    async def on_close(self) -> None:
        await self._flush()

    def pipeline_stats(self) -> dict:
        return {
            "records_created": self._written.created,
            "records_changed": self._written.changed,
            "records_unchanged": self._written.unchanged,
            "items_rejected": self._rejected,
            "items_unarchived": self._unarchived,
            "rejected_by_field": dict(self._rejected_fields),
            "rejected_samples": self._rejected_samples,
        }
