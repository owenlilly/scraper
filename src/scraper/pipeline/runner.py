"""Run one source once: record the run, crawl, store the outcome."""

from sqlalchemy import Engine

from .blobs import FilesystemBlobStore
from .domain import Run
from .spider import RunContext, SourceSpider
from .uow import UnitOfWork


def run_source(spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, *, worker_id: str = "cli") -> Run:
    """Blocks until the crawl ends. The run row says how it ended: `completed`, `paused` (stopped by Ctrl+C; nothing is lost,
    the records so far are saved) or `failed` (storage broke, or the crawl itself raised)."""
    with UnitOfWork(engine, blobs) as uow:
        source = uow.sources.ensure(spider_cls.name)
        run = uow.runs.start(uow.runs.enqueue(source.id).id, worker_id)
    spider = spider_cls(ctx=RunContext(engine, blobs, source.id, run.id))
    try:
        result = spider.start()
    except Exception as error:
        status, stats, message = "failed", spider.pipeline_stats(), f"crawl raised: {error!r}"
    else:
        stats = {**result.stats.to_dict(), **spider.pipeline_stats()}
        status = "failed" if spider.write_error else "paused" if result.paused else "completed"
        message = spider.write_error
    with UnitOfWork(engine, blobs) as uow:
        return uow.runs.finish(run.id, status, stats, message)
