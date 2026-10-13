"""`python -m scraper.pipeline run <source>` crawls one source once. `worker` runs queued runs, `exec-run` is what the worker starts for
each (not for typing). `migrate` brings the database up to date. `list` names the sources."""

import argparse
import signal
import sys
import threading

from scraper.config import load

from .blobs import FilesystemBlobStore
from .db import make_engine
from .migrate import current_revision, upgrade
from .repositories import SourceBusy
from .runner import run_source
from .sources import SPIDERS
from .worker import Worker, exec_run


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _summary(finished) -> str:
    stats = finished.stats
    return (
        f"run {finished.id} {finished.status}: {stats.get('requests_count', 0)} requests, "
        f"{stats.get('records_created', 0)} new, {stats.get('records_changed', 0)} changed, "
        f"{stats.get('records_unchanged', 0)} unchanged, {stats.get('items_rejected', 0)} rejected"
        + (f"\n{finished.error}" if finished.error else "")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scraper.pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="name the available sources")
    commands.add_parser("migrate", help="bring the pipeline schema up to date")
    run = commands.add_parser("run", help="crawl one source once (migrates first)")
    run.add_argument("source", choices=sorted(SPIDERS))
    worker = commands.add_parser("worker", help="run queued runs, each in its own process (migrates first)")
    worker.add_argument("--concurrency", type=_positive, default=1, help="how many runs at once, for different sources")
    child = commands.add_parser("exec-run", help="execute one claimed run; the worker starts this, you do not")
    child.add_argument("run_id", type=int)
    args = parser.parse_args(argv)

    if args.command == "list":
        print("\n".join(sorted(SPIDERS)))
        return 0

    cfg = load()
    if not cfg.database_url:
        print("Set SCRAPER_DATABASE_URL (see .env.example).", file=sys.stderr)
        return 2
    engine = make_engine(cfg.database_url)
    blobs = FilesystemBlobStore(cfg.pipeline_blob_dir)
    try:
        upgrade(engine)
        if args.command == "migrate":
            print(f"pipeline schema at revision {current_revision(engine)}")
            return 0
        if args.command == "worker":
            stop = threading.Event()
            for number in (signal.SIGINT, signal.SIGTERM):
                signal.signal(number, lambda *_: stop.set())
            print(f"worker started, running up to {args.concurrency} run(s) at a time; Ctrl+C stops it after pausing its runs")
            Worker(engine, blobs, concurrency=args.concurrency).serve(stop)
            return 0
        if args.command == "exec-run":
            finished = exec_run(args.run_id, engine, blobs, SPIDERS)
            print(_summary(finished))
            return 0 if finished.status in ("completed", "paused") else 1
        try:
            finished = run_source(SPIDERS[args.source], engine, blobs)
        except SourceBusy:
            print("A run is already queued or running for this source.", file=sys.stderr)
            return 2
    finally:
        engine.dispose()
    print(_summary(finished))
    return 0 if finished.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
