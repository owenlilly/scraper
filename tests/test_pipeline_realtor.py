"""The realtor.ca spider's parsing, paging, block detection and listing schema, on hand-built pages. No browser, network or database:
the markup below follows the selectors the spider uses, so these tests pin the spider's logic, not realtor.ca's current HTML."""

import asyncio

import pytest
from decimal import Decimal
from pydantic import ValidationError
from scrapling.spiders import Request, SessionManager
from scrapling.engines.toolbelt.custom import Response

from scraper.pipeline.items import prepare
from scraper.pipeline.settings import SettingsInvalid, describe_form, load_settings
from scraper.pipeline.sources import SPIDERS
from scraper.pipeline.sources.spiders import realtor_ca
from scraper.pipeline.sources.spiders.realtor_ca import DEFAULT_PAGES, Listing, Location, RealtorSettings, RealtorSpider
from scraper.pipeline.spider import RunContext, SourceSpider

# A spider is built with a run context; parsing and block detection touch neither its database nor its blob store.
CTX = RunContext(engine=None, blobs=None, source_id=1, run_id=1)


def toronto(page: int) -> str:
    return (
        "https://www.realtor.ca/map#view=list&CurrentPage=%d&Sort=6-D&GeoIds=g30_dpz89rm7&GeoName=Toronto%%2C%%20ON"
        "&PropertyTypeGroupID=1&TransactionTypeId=2&PropertySearchTypeId=1&Currency=CAD" % page
    )


def located(location: dict | None = None, **settings) -> RunContext:
    return RunContext(engine=None, blobs=None, source_id=1, run_id=1, settings=RealtorSettings(**settings), location=location)


def starts(spider) -> list:
    async def run():
        return [request async for request in spider.start_requests()]

    return asyncio.run(run())

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


def test_the_spider_declares_its_site_and_starts_from_the_first_page_of_its_location(spider):
    assert spider.allowed_domains == {"realtor.ca"}
    (first,) = starts(spider)
    assert first.url == toronto(1) and first.sid == "stealth1" and first.meta == {"page": 1}


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
    assert [out for out in collect(spider, page(2, current=DEFAULT_PAGES)) if isinstance(out, Request)] == []


def test_every_page_up_to_max_pages_has_a_session_and_no_page_shares_one():
    manager = SessionManager()
    RealtorSpider(ctx=CTX).configure_sessions(manager)
    assert [f"stealth{n}" for n in range(1, DEFAULT_PAGES + 1)] == sorted(manager.session_ids)
    assert len(manager.session_ids) == DEFAULT_PAGES


def test_page_urls_differ_only_in_the_fragment_so_fingerprints_must_keep_it(spider):
    first, second = (Request(spider.page_url(n)) for n in (2, 3))
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
    assert len(created) == DEFAULT_PAGES
    for options in created:
        assert options["disable_resources"] is True and options["block_ads"] is True
        assert options["headless"] is True and options["network_idle"] is True and options["solve_cloudflare"] is True


def test_the_default_settings_are_toronto_and_three_pages():
    settings = RealtorSettings()
    assert [(place.geo_id, place.geo_name) for place in settings.locations] == [("g30_dpz89rm7", "Toronto, ON")]
    assert settings.max_pages == 3


def test_a_location_needs_both_values_trimmed_and_bounded():
    for bad in ({"geo_id": "", "geo_name": "X"}, {"geo_id": "   ", "geo_name": "X"}, {"geo_id": "a", "geo_name": ""}, {"geo_id": "x" * 101, "geo_name": "X"}):
        with pytest.raises(ValidationError):
            Location(**bad)
    kept = Location(geo_id="  g1  ", geo_name=" Ottawa, ON ")
    assert (kept.geo_id, kept.geo_name) == ("g1", "Ottawa, ON")


@pytest.mark.parametrize("odd", ["g30_dpz89rm7", "12345", "weird id with spaces & symbols #?", "ünïcode"])
def test_no_geoid_format_is_enforced_because_the_format_is_not_ours(odd):
    assert Location(geo_id=odd, geo_name="X").geo_id == odd


def test_the_geoid_and_geoname_mapping_is_one_to_one():
    place = lambda geo_id, geo_name: {"geo_id": geo_id, "geo_name": geo_name}
    with pytest.raises(SettingsInvalid) as same_id:
        load_settings(RealtorSpider, {"locations": [place("a", "A"), place("b", "B"), place("a", "C")]})
    assert same_id.value.message == "locations: Location 3 repeats the GeoId of location 1."
    with pytest.raises(SettingsInvalid) as same_name:
        load_settings(RealtorSpider, {"locations": [place("a", "A"), place("b", "A")]})
    assert same_name.value.message == "locations: Location 2 repeats the GeoName of location 1."


def test_at_most_a_hundred_locations_and_none_is_allowed():
    place = lambda n: {"geo_id": f"g{n}", "geo_name": f"City {n}"}
    assert len(load_settings(RealtorSpider, {"locations": [place(n) for n in range(100)]}).locations) == 100
    with pytest.raises(SettingsInvalid) as caught:
        load_settings(RealtorSpider, {"locations": [place(n) for n in range(101)]})
    assert caught.value.message.startswith("locations:")
    assert load_settings(RealtorSpider, {"locations": []}).locations == []


@pytest.mark.parametrize("pages, ok", [(0, False), (1, True), (10, True), (11, False)])
def test_pages_per_run_is_one_to_ten(pages, ok):
    if ok:
        assert load_settings(RealtorSpider, {"max_pages": pages}).max_pages == pages
    else:
        with pytest.raises(SettingsInvalid):
            load_settings(RealtorSpider, {"max_pages": pages})


def test_the_form_description_names_the_locations_table_and_the_pages_field():
    locations, pages = describe_form(RealtorSpider)
    assert (locations["key"], locations["label"], locations["maxItems"]) == ("locations", "Locations", 100)
    assert [(item["key"], item["label"], item["maxLength"]) for item in locations["items"]] == [("geo_id", "GeoId", 100), ("geo_name", "GeoName", 100)]
    assert (pages["key"], pages["label"], pages["minimum"], pages["maximum"], pages["default"]) == ("max_pages", "Pages per run", 1, 10, 3)


def test_the_default_run_builds_exactly_the_addresses_it_always_did():
    spider = RealtorSpider(ctx=CTX)
    assert spider.page_url(1) == toronto(1) and spider.page_url(2) == toronto(2)


def test_a_locations_values_are_url_encoded_and_cannot_change_the_rest_of_the_address():
    place = {"geo_id": "g1&Sort=1#x/y%z", "geo_name": "Saint-Jean, QC & co/ünï"}
    url = RealtorSpider(ctx=located(place)).page_url(2)
    assert "GeoIds=g1%26Sort%3D1%23x%2Fy%25z&GeoName=Saint-Jean%2C%20QC%20%26%20co%2F%C3%BCn%C3%AF&" in url
    assert url.startswith("https://www.realtor.ca/map#view=list&CurrentPage=2&Sort=6-D&GeoIds=")
    assert url.count("#") == 1 and url.count("&Sort=") == 1 and url.endswith("&PropertyTypeGroupID=1&TransactionTypeId=2&PropertySearchTypeId=1&Currency=CAD")


def test_the_run_crawls_its_own_location_from_the_first_page_to_the_next():
    ottawa = {"geo_id": "g2_abc", "geo_name": "Ottawa, ON"}
    spider = RealtorSpider(ctx=located(ottawa))
    (first,) = starts(spider)
    assert "GeoIds=g2_abc&GeoName=Ottawa%2C%20ON" in first.url
    follow = [out for out in collect(spider, page(2, current=1)) if isinstance(out, Request)]
    assert len(follow) == 1 and "CurrentPage=2" in follow[0].url and "GeoIds=g2_abc" in follow[0].url


def test_pages_per_run_sets_how_many_sessions_open_and_where_paging_stops(monkeypatch):
    created = []

    class Recorder:
        def __init__(self, **options):
            created.append(options)

    monkeypatch.setattr(realtor_ca, "AsyncStealthySession", Recorder)
    spider = RealtorSpider(ctx=located(None, max_pages=5))
    assert len(created) == 5
    assert [out for out in collect(spider, page(2, current=4)) if isinstance(out, Request)][0].meta["page"] == 5
    assert [out for out in collect(spider, page(2, current=5)) if isinstance(out, Request)] == []
