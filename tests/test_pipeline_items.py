import pytest
from pydantic import ValidationError
from scrapling.parser import Selector

from pipeline_support import book, page_html
from scraper.pipeline.blobs import FilesystemBlobStore
from scraper.pipeline.items import prepare
from scraper.pipeline.sources.books_toscrape import Book, BooksToScrape

RAW = {"title": "  A  Light in the Attic ", "url": "https://x/a", "price": "£51.77", "rating": "star-rating Three", "in_stock": True}


def test_prepare_normalises_before_it_keys_and_hashes():
    item = prepare(Book, ("url",), RAW, fetch_id=7)
    assert item.natural_key == "https://x/a"
    assert item.data == {"title": "A Light in the Attic", "url": "https://x/a", "price": "51.77", "rating": 3, "in_stock": True}
    assert item.fetch_id == 7


def test_a_page_that_only_changed_its_whitespace_is_not_a_new_version():
    tidy = prepare(Book, ("url",), {**RAW, "title": "A Light in the Attic"})
    messy = prepare(Book, ("url",), RAW)
    assert tidy.content_hash == messy.content_hash


def test_a_changed_price_changes_the_hash():
    assert prepare(Book, ("url",), RAW).content_hash != prepare(Book, ("url",), {**RAW, "price": "£52.00"}).content_hash


def test_the_mis_decoded_pound_sign_still_parses():
    assert prepare(Book, ("url",), {**RAW, "price": "Â£51.77"}).data["price"] == "51.77"


def test_an_item_that_does_not_fit_the_schema_raises():
    with pytest.raises(ValidationError) as caught:
        prepare(Book, ("url",), {**RAW, "price": "n/a"})
    assert caught.value.errors()[0]["loc"] == ("price",)


def test_a_composite_natural_key_joins_its_fields():
    assert prepare(Book, ("title", "url"), RAW).natural_key == "A Light in the Attic|https://x/a"


def test_the_books_parser_reads_a_page_and_follows_next():
    page = Selector(page_html([book(1, price="£5.00", rating="Five"), book(2, in_stock=False)], "page-2.html"), url="http://h/catalogue/page-1.html")
    import asyncio

    class Fake:
        def __init__(self, selector):
            self.css, self.urljoin = selector.css, selector.urljoin

        def follow(self, href):
            return ("follow", href)

    async def collect():
        return [result async for result in BooksToScrape.parse(None, Fake(page))]

    first, second, follow = asyncio.run(collect())
    assert first["title"] == "Book 1" and first["price"] == "£5.00" and first["rating"] == "star-rating Five"
    assert first["url"] == "http://h/catalogue/book-1_1/index.html" and first["in_stock"] is True
    assert second["in_stock"] is False
    assert follow == ("follow", "page-2.html")


def test_the_blob_store_keeps_identical_bodies_once_and_returns_them_intact(tmp_path):
    store = FilesystemBlobStore(tmp_path)
    body = b"<html>" + b"x" * 5000 + b"</html>"
    first = store.put(body)
    assert store.put(body) == first
    assert store.get(first) == body
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(files) == 1 and files[0].stat().st_size < len(body)  # stored once, and compressed
    assert store.put(b"other") != first


def test_a_nul_character_from_a_page_is_removed_because_postgres_cannot_store_it():
    item = prepare(Book, ("url",), {**RAW, "title": "Bad\x00 title", "url": "https://x/\x00a"})
    assert item.data["title"] == "Bad title" and item.natural_key == "https://x/a"
    assert "\x00" not in item.data["url"]
