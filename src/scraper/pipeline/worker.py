"""The worker: claims queued runs and runs each in a child process. A child, because `Spider.start()` owns its event loop and
installs a SIGINT handler, and because a browser-backed spider needs memory it can give back when it ends."""

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import timedelta
from typing import Callable, Protocol

from sqlalchemy import Engine

from .blobs import FilesystemBlobStore
from .domain import Run
from .runner import execute
from .spider import SourceSpider
from .uow import UnitOfWork

log = logging.getLogger("scraper.pipeline.worker")
STALE_AFTER = timedelta(seconds=60)
POLL_SECONDS = 2.0
GRACE_SECONDS = 30.0


class Child(Protocol):
    def poll(self) -> int | None: ...
    def send_signal(self, number: int) -> None: ...
    def kill(self) -> None: ...


def spawn_child(run_id: int) -> subprocess.Popen:
    """Its own session, so Ctrl+C in the worker's terminal reaches the worker, which then stops its children one by one."""
    return subprocess.Popen([sys.executable, "-m", "scraper.pipeline", "exec-run", str(run_id)], start_new_session=True)


def exec_run(run_id: int, engine: Engine, blobs: FilesystemBlobStore, spiders: dict[str, type[SourceSpider]] | None = None) -> Run:
    """Run a claimed run to its end in this process. This is what the child process does."""
    if spiders is None:
        from .sources import SPIDERS as spiders
    with UnitOfWork(engine, blobs) as uow:
        run = uow.runs.get(run_id)
        source = uow.sources.by_id(run.source_id) if run else None
    if run is None or source is None:
        raise LookupError(f"there is no run {run_id}")
    if run.status != "running":
        raise ValueError(f"run {run_id} is {run.status}, not running")
    return execute(spiders[source.name], engine, blobs, source.id, run)


class Worker:
    def __init__(
        self, engine: Engine, blobs: FilesystemBlobStore, *, concurrency: int = 1, launch: Callable[[int], Child] = spawn_child,
        stale_after: timedelta = STALE_AFTER, worker_id: str | None = None,
    ) -> None:
        self.engine, self.blobs, self.concurrency, self.launch, self.stale_after = engine, blobs, concurrency, launch, stale_after
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.children: dict[int, Child] = {}

    def tick(self) -> int:
        """One pass: note finished processes, fail runs whose process went quiet, then claim runs while there is room. Returns how many started."""
        self._collect()
        with UnitOfWork(self.engine, self.blobs) as uow:
            for run_id in uow.runs.reap_stale(self.stale_after):
                log.warning("run %s went quiet and was failed", run_id)
        started = 0
        while len(self.children) < self.concurrency:
            with UnitOfWork(self.engine, self.blobs) as uow:
                run = uow.runs.claim_next(self.worker_id)
            if run is None:
                break
            try:
                self.children[run.id] = self.launch(run.id)
            except OSError as error:
                with UnitOfWork(self.engine, self.blobs) as uow:
                    uow.runs.finish(run.id, "failed", {}, f"could not start the run process: {error}")
                continue
            started += 1
            log.info("started run %s", run.id)
        return started

    def _collect(self) -> None:
        for run_id, child in list(self.children.items()):
            code = child.poll()
            if code is None:
                continue
            del self.children[run_id]
            with UnitOfWork(self.engine, self.blobs) as uow:
                run = uow.runs.get(run_id)
                if run is not None and run.status == "running":  # the process ended without finishing its run
                    uow.runs.finish(run_id, "failed", run.stats, f"the run process exited with code {code} before it finished")

    def serve(self, stop: threading.Event, poll_seconds: float = POLL_SECONDS) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("a worker pass failed; trying again")  # a database blip must not end the worker
            stop.wait(poll_seconds)
        self.shutdown()

    def shutdown(self, grace: float = GRACE_SECONDS) -> None:
        """Ask each process to finish what is in flight and stop (its run ends `paused`), wait, then kill what is left."""
        for child in self.children.values():
            child.send_signal(signal.SIGINT)
        deadline = time.monotonic() + grace
        while self.children and time.monotonic() < deadline:
            self._collect()
            time.sleep(0.05)
        for child in self.children.values():
            child.kill()
        time.sleep(0.05)
        self._collect()
