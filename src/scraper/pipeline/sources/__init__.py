"""The spiders, one module per site. Add a spider to SPIDERS to make it runnable by name."""

from ..spider import SourceSpider
from .books_toscrape import BooksToScrape
from .spiders.realtor_ca import RealtorSpider

SPIDERS: dict[str, type[SourceSpider]] = {spider.name: spider for spider in (BooksToScrape, RealtorSpider)}
