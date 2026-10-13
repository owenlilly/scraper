"""realtor.ca listings for Toronto, read from the search page's cards.

realtor.ca blocks most automated clients, so this spider drives a real (stealth) browser, one session per page, and treats a page
with no cards as blocked. It needs the browsers installed on the machine that runs it (`scrapling install`)."""

import re
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, Field, field_validator
from scrapling import Selector
from scrapling.fetchers import AsyncStealthySession
from scrapling.spiders import Response, SessionManager

from ...spider import SourceSpider

MAX_PAGES = 3
SITE = "https://www.realtor.ca"


class Listing(BaseModel):
    """One card of the search results. A listing with no id cannot be told from another, so it is rejected; everything else the card
    may lack is simply unknown."""

    id: str = Field(min_length=1)
    address: str
    price: Decimal | None = None
    details_url: str | None = None
    image_url: str | None = None
    mls_no: str | None = None
    data_value: str | None = None

    @field_validator("address", mode="before")
    @classmethod
    def _address(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, value):
        """'$1,249,000' or '$2,450 / Monthly' to a number. Text with no figure in it ('Contact for price') is an unknown price."""
        if isinstance(value, str):
            figure = re.search(r"\d[\d,]*(?:\.\d+)?", value)
            return Decimal(figure.group().replace(",", "")) if figure else None
        return value


class RealtorSpider(SourceSpider):
    name = "realtor_ca"
    start_urls = ["https://www.realtor.ca/on/toronto/real-estate"]
    allowed_domains = {"realtor.ca"}
    item_schema = Listing
    natural_key = ("id",)
    parser_version = "1"

    next_url = 'https://www.realtor.ca/map#view=list&CurrentPage={page}&Sort=6-D&GeoIds=g30_dpz89rm7&GeoName=Toronto%2C%20ON&PropertyTypeGroupID=1&TransactionTypeId=2&PropertySearchTypeId=1&Currency=CAD'

    # Not decided yet whether this spider honours realtor.ca's robots.txt, so for now it neither fetches nor consults it. (The pipeline's
    # default is to obey; with this off Scrapling also ignores any crawl-delay the file would set, leaving only the autothrottle.)
    robots_txt_obey = False

    # The hash differs per page, but fingerprints drop fragments by default and would dedupe pages 2+.
    fp_keep_fragments = True
    # A retry reuses the already-used session, which realtor.ca blocks, so retrying is pointless.
    max_blocked_retries = 1

    def configure_sessions(self, manager: SessionManager):
        # realtor.ca blocks the search API on any 2nd navigation within the same browser session,
        # so each page gets its own session (page N is fetched through "stealth{N}").
        # No images, fonts, stylesheets or ads: realtor.ca's page does not fire the browser's load event in reasonable time while it
        # fetches them (60 s timeout, then a retry: about three minutes a page). The cards and their image addresses are in the HTML.
        for page in range(1, MAX_PAGES + 1):
            manager.add(
                f"stealth{page}",
                AsyncStealthySession(headless=True, network_idle=True, solve_cloudflare=True, disable_resources=True, block_ads=True),
                lazy=True,
            )

    async def looks_blocked(self, response: Response) -> bool:
        # A blocked search still returns 200 with an empty list, so check for the cards themselves.
        return await super().looks_blocked(response) or not response.css('.listingCard')

    async def parse(self, response: Response):
        page = response.meta.get('page', 1)
        for item in response.css('.listingCard'):
            yield self._extract_listing_card(item)

        if page < MAX_PAGES:
            yield response.follow(self.next_url.format(page=page + 1), sid=f"stealth{page + 1}",
                                  wait_selector='.listingCard', callback=self.parse, meta={'page': page + 1})

    @staticmethod
    def _extract_listing_card(item: Selector) -> dict[Any, Any]:
        extracted = RealtorSpider._extract_id(item)
        _id = None
        data_value = None
        if extracted:
            _id, data_value = extracted

        href = item.css(".listingDetailsLink::attr(href)").get()
        return {
            'id': _id,
            'address': RealtorSpider._text(item, '.listingCardAddress::text'),
            'price': RealtorSpider._text(item, '.listingCardPrice::text'),
            'details_url': urljoin(SITE, href) if href else None,
            'image_url': item.css('img.listingCardImage::attr(src)').get(),
            'mls_no': RealtorSpider._text(item, '.listingCardMLS span::text'),
            'data_value': data_value,
        }

    @staticmethod
    def _text(item: Selector, selector: str) -> str | None:
        """The trimmed text, or None when the card has none. (Not the text 'None': that would be stored as an address.)"""
        text = item.css(selector).get()
        return text.strip() or None if text else None

    @staticmethod
    def _extract_id(item: Selector) -> tuple[str, str] | None:
        data_value = item.css('.propertyCardDetailsNoteIcon::attr(data-value)').get()
        if not data_value:
            return None
        parts = data_value.split('_')
        return parts[0], data_value
