"""Run one source once: record the run, crawl, store the outcome."""

from sqlalchemy import Engine

from .blobs import FilesystemBlobStore
from .domain import Run
from .spider import RunContext, SourceSpider
from .uow import UnitOfWork


def run_source(
    spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, *, worker_id: str = "cli", kind: str = "live", options: dict | None = None
) -> Run:
    """Blocks until the crawl ends. The run row says how it ended: `completed`, `paused` (stopped; nothing is lost, the records so
    far are saved) or `failed` (storage broke, or the crawl itself raised). Raises `SourceBusy` if the source already has a run in flight."""
    with UnitOfWork(engine, blobs) as uow:
        source = uow.sources.ensure(spider_cls.name)
        run = uow.runs.start(uow.runs.enqueue(source.id, kind=kind, options=options).id, worker_id)
    return execute(spider_cls, engine, blobs, source.id, run)


def execute(spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, source_id: int, run: Run) -> Run:
    """Crawl for a run that is already `running` and finish it."""
    spider = spider_cls(ctx=RunContext(engine, blobs, source_id, run.id, run.kind, run.options))
    try:
        result = spider.start()
    except Exception as error:
        status, stats, message = "failed", spider.pipeline_stats(), f"crawl raised: {error!r}"
    else:
        stats = {**result.stats.to_dict(), **spider.pipeline_stats()}
        status = _status(spider, result.paused)
        message = spider.write_error
    with UnitOfWork(engine, blobs) as uow:
        return uow.runs.finish(run.id, status, stats, message)


def _status(spider: SourceSpider, paused: bool) -> str:
    if spider.write_error:
        return "failed"
    if spider.stopped_at_cap:
        return "completed"  # a trial that reached its cap did what it was asked
    return "paused" if paused else "completed"
