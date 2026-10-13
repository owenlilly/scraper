"""A placeholder source: books.toscrape.com is a practice site built to be scraped, so the pipeline can be exercised without
touching a real business's pages. Replace it with a real source once one is chosen."""

import re
from decimal import Decimal

from pydantic import BaseModel, field_validator

from ..spider import SourceSpider

RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


class Book(BaseModel):
    title: str
    url: str
    price: Decimal
    rating: int
    in_stock: bool

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, value):
        """'£51.77' (or the same text mis-decoded as 'Â£51.77') to a number."""
        if isinstance(value, str):
            digits = re.sub(r"[^\d.]", "", value)
            return digits or value
        return value

    @field_validator("rating", mode="before")
    @classmethod
    def _rating(cls, value):
        """The site writes the rating as a class name: 'star-rating Three'."""
        if isinstance(value, str):
            word = value.split()[-1] if value.split() else ""
            return RATINGS.get(word, value)
        return value


class BooksToScrape(SourceSpider):
    name = "books_toscrape"
    start_urls = ["https://books.toscrape.com/catalogue/page-1.html"]
    allowed_domains = {"books.toscrape.com"}
    item_schema = Book
    natural_key = ("url",)
    parser_version = "1"
    autothrottle_start_delay = 0.5  # a practice site, built to be fetched; a real source keeps the slow default

    async def parse(self, response):
        for pod in response.css("article.product_pod"):
            yield {
                "title": pod.css("h3 a::attr(title)").get(),
                "url": response.urljoin(pod.css("h3 a::attr(href)").get() or ""),
                "price": pod.css("p.price_color::text").get(),
                "rating": pod.css("p.star-rating").first.attrib.get("class"),
                "in_stock": "In stock" in " ".join(pod.css("p.availability::text").getall()),
            }
        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield response.follow(next_page)
