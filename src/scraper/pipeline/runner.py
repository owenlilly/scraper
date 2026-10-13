"""Run one source once: record the run, crawl while reporting progress, store the outcome."""

import threading

from sqlalchemy import Engine

from .blobs import FilesystemBlobStore
from .domain import Run
from .spider import RunContext, SourceSpider
from .uow import UnitOfWork

BEAT_SECONDS = 2.0


def run_source(
    spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, *,
    worker_id: str = "cli", kind: str = "live", options: dict | None = None, beat_seconds: float = BEAT_SECONDS,
) -> Run:
    """Blocks until the crawl ends. The run row says how it ended: `completed`, `paused` (stopped; nothing is lost, the records so
    far are saved) or `failed` (storage broke, or the crawl itself raised). Raises `SourceBusy` if the source already has a run in flight."""
    with UnitOfWork(engine, blobs) as uow:
        source = uow.sources.ensure(spider_cls.name)
        run = uow.runs.start(uow.runs.enqueue(source.id, kind=kind, options=options).id, worker_id)
    return execute(spider_cls, engine, blobs, source.id, run, beat_seconds=beat_seconds)


def execute(
    spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, source_id: int, run: Run, *, beat_seconds: float = BEAT_SECONDS
) -> Run:
    """Crawl for a run that is already `running`, reporting progress as it goes, and finish it. The command line and the worker's
    child process both come through here, so both heartbeat and both can be stopped from the page."""
    try:
        spider = spider_cls(ctx=RunContext(engine, blobs, source_id, run.id, run.kind, run.options))
    except Exception as error:
        with UnitOfWork(engine, blobs) as uow:
            return uow.runs.finish(run.id, "failed", {}, f"could not start the spider: {error!r}")
    stop = threading.Event()
    beat = threading.Thread(target=_beat, args=(stop, spider, engine, blobs, run.id, beat_seconds), name=f"heartbeat-{run.id}", daemon=True)
    beat.start()
    try:
        result = spider.start()
    except Exception as error:
        status, stats, message = "failed", spider.pipeline_stats(), f"crawl raised: {error!r}"
    else:
        stats = {**result.stats.to_dict(), **spider.pipeline_stats()}
        status = _status(spider, result.paused or spider.pause_was_requested)
        message = spider.write_error
    finally:
        stop.set()
        beat.join(timeout=10)
    with UnitOfWork(engine, blobs) as uow:
        return uow.runs.finish(run.id, status, stats, message)


def _beat(stop: threading.Event, spider: SourceSpider, engine: Engine, blobs: FilesystemBlobStore, run_id: int, every: float) -> None:
    """Write the spider's numbers into the run, and pass a stop request on to it. A beat that fails is skipped: the next one tries
    again, and if the silence lasts the reaper fails the run."""
    while not stop.wait(every):
        try:
            with UnitOfWork(engine, blobs) as uow:
                should_stop = uow.runs.heartbeat(run_id, spider.snapshot())
        except Exception:
            continue
        if should_stop:
            spider.stop_once()


def _status(spider: SourceSpider, paused: bool) -> str:
    if spider.write_error:
        return "failed"
    if spider.stopped_at_cap:
        return "completed"  # a trial that reached its cap did what it was asked
    return "paused" if paused else "completed"
