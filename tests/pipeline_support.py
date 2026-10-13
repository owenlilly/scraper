"""A local copy of the practice site, so pipeline tests crawl real HTTP without touching the internet."""

import functools
import logging
import time
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from scraper.pipeline.sources.books_toscrape import BooksToScrape

POD = """<article class="product_pod">
<h3><a href="{slug}/index.html" title="{title}">{title:.14}...</a></h3>
<p class="star-rating {rating}"><i class="icon-star"></i></p>
<div class="product_price"><p class="price_color">{price}</p>
<p class="instock availability"><i class="icon-ok"></i>
    {stock}
</p></div></article>"""


def book(n: int, *, price: str = "£10.00", rating: str = "Three", in_stock: bool = True) -> dict:
    return {"slug": f"book-{n}_{n}", "title": f"Book {n}", "price": price, "rating": rating, "stock": "In stock" if in_stock else "Out of stock"}


def page_html(books: list[dict], next_page: str | None) -> str:
    pods = "\n".join(POD.format(**item) for item in books)
    link = f'<ul class="pager"><li class="next"><a href="{next_page}">next</a></li></ul>' if next_page else ""
    return f'<html><head><meta charset="utf-8"></head><body><ol class="row">{pods}</ol>{link}</body></html>'


def write_site(root: Path, pages: list[list[dict]], *, chained: bool = True) -> None:
    """`catalogue/page-N.html` for each list of books. `chained` links each page to the next, as the real site does; without it
    every page stands alone, so a spider given all their addresses fetches them side by side."""
    catalogue = root / "catalogue"
    catalogue.mkdir(parents=True, exist_ok=True)
    for index, books in enumerate(pages, start=1):
        following = f"page-{index + 1}.html" if chained and index < len(pages) else None
        (catalogue / f"page-{index}.html").write_text(page_html(books, following), encoding="utf-8")


class _Quiet(SimpleHTTPRequestHandler):
    delay = 0.0

    def do_GET(self):
        time.sleep(self.delay)
        super().do_GET()

    def log_message(self, *args):
        pass


@contextmanager
def serve(root: Path, *, delay: float = 0.0):
    handler = functools.partial(type("Handler", (_Quiet,), {"delay": delay}), directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def local_books(base: str, *, pages: int = 1, **attrs):
    """The books spider pointed at `base`. `pages` > 1 starts from every page address, for crawls that fetch side by side."""
    start = [f"{base}/catalogue/page-{n}.html" for n in range(1, pages + 1)]
    settings = {
        "start_urls": start, "allowed_domains": {urlparse(base).netloc}, "autothrottle_enabled": False,
        "logging_level": logging.CRITICAL,
    }
    return type("LocalBooks", (BooksToScrape,), {**settings, **attrs})
