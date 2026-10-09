"""Breadth-first, same-domain link following."""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urlparse

from .fetch import FetchError, Page


@dataclass
class CrawlResult:
    pages: list[Page] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def normalize(url: str) -> str:
    base, _ = urldefrag(url)
    parts = urlparse(base)
    return parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower(), path=parts.path or "/").geturl()


def _host(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.removeprefix("www.")


def same_domain(a: str, b: str) -> bool:
    return _host(a) == _host(b)


def crawl(
    seed: Page,
    *,
    fetch_fn: Callable[[str], Page],
    depth: int = 1,
    max_pages: int = 20,
    delay: float = 0.0,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CrawlResult:
    result = CrawlResult(pages=[seed])
    seen = {normalize(seed.url)}
    queue = deque((link, 1) for link in seed.links) if depth >= 1 else deque()
    if on_progress:
        on_progress(1, max_pages)
    while queue and len(result.pages) < max_pages:
        if should_cancel and should_cancel():
            break
        url, level = queue.popleft()
        key = normalize(url)
        if key in seen or not same_domain(url, seed.url):
            continue
        seen.add(key)
        if delay:
            sleep(delay)
        try:
            page = fetch_fn(url)
        except FetchError as exc:
            result.failed.append((url, exc.message))
            continue
        landed = normalize(page.url)
        if landed != key:
            if landed in seen:
                continue
            seen.add(landed)
        result.pages.append(page)
        if on_progress:
            on_progress(len(result.pages), max_pages)
        if level < depth:
            queue.extend((link, level + 1) for link in page.links)
    return result
