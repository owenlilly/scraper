"""The realtor.ca spider's parsing, paging, block detection and listing schema, on hand-built pages. No browser, network or database:
the markup below follows the selectors the spider uses, so these tests pin the spider's logic, not realtor.ca's current HTML."""

import asyncio

import pytest
from decimal import Decimal
from pydantic import ValidationError
from scrapling.spiders import Request, SessionManager
from scrapling.engines.toolbelt.custom import Response

from scraper.pipeline.items import prepare
from scraper.pipeline.sources import SPIDERS
from scraper.pipeline.sources.spiders import realtor_ca
from scraper.pipeline.sources.spiders.realtor_ca import MAX_PAGES, Listing, RealtorSpider
from scraper.pipeline.spider import RunContext, SourceSpider

# A spider is built with a run context; parsing and block detection touch neither its database nor its blob store.
CTX = RunContext(engine=None, blobs=None, source_id=1, run_id=1)

CARD = """<div class="listingCard">
  <a class="listingDetailsLink" href="/real-estate/{n}/listing-{n}">
    <img class="listingCardImage" src="https://img.example/{n}.jpg">
    <div class="listingCardPrice">  $1,249,000 </div>
    <div class="listingCardAddress">  {n} Main St, Toronto </div>
    <div class="listingCardMLS"><span>C{n:07d}</span></div>
  </a>
  <span class="propertyCardDetailsNoteIcon" data-value="{n}_note_x"></span>
</div>"""


def page(cards: int, *, first: int = 1, status: int = 200, current: int = 1, html: str | None = None) -> Response:
    html = html or "<html><body>" + "".join(CARD.format(n=first + i) for i in range(cards)) + "</body></html>"
    url = "https://www.realtor.ca/map#view=list&CurrentPage=%d" % current
    response = Response(
        url=url, content=html, status=status, reason="", cookies={}, headers={}, request_headers={}, meta={"page": current},
    )
    response.request = Request(url, sid=f"stealth{current}", callback=None)
    return response


def collect(spider, response) -> list:
    async def run():
        return [item async for item in spider.parse(response)]

    return asyncio.run(run())


@pytest.fixture
def spider():
    return RealtorSpider(ctx=CTX)


def test_the_spider_declares_its_site_and_start_page(spider):
    assert spider.allowed_domains == {"realtor.ca"}
    assert spider.start_urls == ["https://www.realtor.ca/on/toronto/real-estate"]


def test_each_card_becomes_one_listing_dict(spider):
    items = [item for item in collect(spider, page(3)) if isinstance(item, dict)]
    assert len(items) == 3
    assert items[0] == {
        "id": "1", "address": "1 Main St, Toronto", "price": "$1,249,000",
        "details_url": "https://www.realtor.ca/real-estate/1/listing-1", "image_url": "https://img.example/1.jpg",
        "mls_no": "C0000001", "data_value": "1_note_x",
    }
    assert [item["mls_no"] for item in items] == ["C0000001", "C0000002", "C0000003"]


def test_a_card_without_the_note_icon_has_no_id(spider):
    bare = '<div class="listingCard"><div class="listingCardAddress">1 Main St</div><div class="listingCardPrice">$1</div></div>'
    items = [item for item in collect(spider, page(0, html=bare)) if isinstance(item, dict)]
    assert items[0]["id"] is None and items[0]["data_value"] is None and items[0]["mls_no"] is None


def test_a_page_follows_to_the_next_page_through_that_pages_own_session(spider):
    follow = [out for out in collect(spider, page(2, current=1)) if isinstance(out, Request)]
    assert len(follow) == 1
    assert follow[0].sid == "stealth2" and follow[0].meta["page"] == 2
    assert "CurrentPage=2" in follow[0].url and follow[0].callback == spider.parse


def test_paging_stops_at_max_pages(spider):
    assert [out for out in collect(spider, page(2, current=MAX_PAGES)) if isinstance(out, Request)] == []


def test_every_page_up_to_max_pages_has_a_session_and_no_page_shares_one():
    manager = SessionManager()
    RealtorSpider(ctx=CTX).configure_sessions(manager)
    assert [f"stealth{n}" for n in range(1, MAX_PAGES + 1)] == sorted(manager.session_ids)
    assert len(manager.session_ids) == MAX_PAGES


def test_page_urls_differ_only_in_the_fragment_so_fingerprints_must_keep_it(spider):
    first, second = (Request(spider.next_url.format(page=n)) for n in (2, 3))
    assert spider.fp_keep_fragments is True
    assert first.update_fingerprint(keep_fragments=True) != second.update_fingerprint(keep_fragments=True)


def test_an_empty_200_is_blocked_and_a_page_of_cards_is_not(spider):
    assert asyncio.run(spider.looks_blocked(page(0))) is True
    assert asyncio.run(spider.looks_blocked(page(2))) is False


def test_a_blocked_status_is_blocked_even_with_cards(spider):
    assert asyncio.run(spider.looks_blocked(page(2, status=403))) is True


def test_a_blocked_page_is_not_retried():
    assert RealtorSpider.max_blocked_retries == 1


def test_the_spider_is_runnable_through_the_pipeline_by_name():
    from scraper.pipeline.spider import SourceSpider

    assert SPIDERS.get("realtor_ca") is realtor_ca.RealtorSpider and issubclass(RealtorSpider, SourceSpider)


def test_the_spider_is_named_for_the_site_and_keyed_by_listing_id():
    assert RealtorSpider.name == "realtor_ca" and RealtorSpider.item_schema is Listing
    assert RealtorSpider.natural_key == ("id",) and RealtorSpider.parser_version == "1"
    assert issubclass(RealtorSpider, SourceSpider)


def test_the_spider_is_listed_once_under_its_own_name():
    assert "demo" not in SPIDERS and list(SPIDERS).count("realtor_ca") == 1


def test_a_scraped_card_is_validated_and_becomes_a_record_keyed_by_its_id(spider):
    (item,) = [item for item in collect(spider, page(1)) if isinstance(item, dict)]
    record = prepare(Listing, RealtorSpider.natural_key, item)
    assert record.natural_key == "1"
    assert record.data["price"] == "1249000" and record.data["address"] == "1 Main St, Toronto" and record.data["mls_no"] == "C0000001"


@pytest.mark.parametrize(
    "text, expected",
    [("$1,249,000", Decimal("1249000")), ("  $899,900 ", Decimal("899900")), ("$2,450 / Monthly", Decimal("2450")),
     ("$1,250.50", Decimal("1250.50")), ("Contact for price", None), ("", None), (None, None)],
)
def test_a_price_is_read_as_a_number_and_a_missing_one_is_unknown_not_an_error(text, expected):
    listing = Listing(id="1", address="1 Main St", price=text)
    assert listing.price == expected


def test_a_card_with_no_id_is_rejected_rather_than_stored_under_the_key_none(spider):
    bare = '<div class="listingCard"><div class="listingCardAddress">1 Main St</div><div class="listingCardPrice">$1</div></div>'
    (item,) = [item for item in collect(spider, page(0, html=bare)) if isinstance(item, dict)]
    with pytest.raises(ValidationError) as caught:
        prepare(Listing, RealtorSpider.natural_key, item)
    assert [error["loc"] for error in caught.value.errors()] == [("id",)]


def test_a_missing_field_is_none_and_never_the_text_none(spider):
    bare = '<div class="listingCard"><span class="propertyCardDetailsNoteIcon" data-value="7_x"></span></div>'
    (item,) = [item for item in collect(spider, page(0, html=bare)) if isinstance(item, dict)]
    assert item["address"] is None and item["price"] is None and item["details_url"] is None
    with pytest.raises(ValidationError) as caught:
        prepare(Listing, RealtorSpider.natural_key, item)
    assert [error["loc"] for error in caught.value.errors()] == [("address",)]  # only the address is required besides the id


def test_robots_txt_is_not_fetched_or_consulted_yet():
    """Whether this spider honours realtor.ca's robots.txt is undecided, so for now the engine builds no robots manager: no request for
    robots.txt, no allow or deny check, no crawl-delay from it. Switch this on (and change this test) once that is decided."""
    assert RealtorSpider.robots_txt_obey is False


def test_every_page_session_skips_images_fonts_stylesheets_and_ads(monkeypatch):
    """realtor.ca's page never fires the browser's load event in reasonable time while it fetches all of that: the first navigation
    timed out at 60 s and succeeded only on a retry, about three minutes per page. Without it a page takes seconds."""
    created = []

    class Recorder:
        def __init__(self, **options):
            created.append(options)

    monkeypatch.setattr(realtor_ca, "AsyncStealthySession", Recorder)
    RealtorSpider(ctx=CTX)  # building a spider configures its sessions
    assert len(created) == MAX_PAGES
    for options in created:
        assert options["disable_resources"] is True and options["block_ads"] is True
        assert options["headless"] is True and options["network_idle"] is True and options["solve_cloudflare"] is True
