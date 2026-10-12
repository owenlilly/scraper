"""`python -m scraper.pipeline run <source>` crawls one source once. `python -m scraper.pipeline list` names the sources."""

import argparse
import sys

from scraper.config import load

from .blobs import FilesystemBlobStore
from .runner import run_source
from .schema import migrate
from .sources import SPIDERS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scraper.pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="name the available sources")
    run = commands.add_parser("run", help="crawl one source once")
    run.add_argument("source", choices=sorted(SPIDERS))
    args = parser.parse_args(argv)

    if args.command == "list":
        print("\n".join(sorted(SPIDERS)))
        return 0

    cfg = load()
    if not cfg.database_url:
        print("Set SCRAPER_DATABASE_URL (see .env.example).", file=sys.stderr)
        return 2
    from scraper.api.db import make_pool

    pool = make_pool(cfg.database_url)
    try:
        migrate(pool)
        finished = run_source(SPIDERS[args.source], pool, FilesystemBlobStore(cfg.pipeline_blob_dir))
    finally:
        pool.close()
    stats = finished.stats
    print(
        f"run {finished.id} {finished.status}: {stats.get('requests_count', 0)} requests, "
        f"{stats.get('records_created', 0)} new, {stats.get('records_changed', 0)} changed, "
        f"{stats.get('records_unchanged', 0)} unchanged, {stats.get('items_rejected', 0)} rejected"
        + (f"\n{finished.error}" if finished.error else "")
    )
    return 0 if finished.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
