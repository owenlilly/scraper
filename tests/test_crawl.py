from fakes import make_fetch, make_page
from scraper.crawl import CrawlResult, crawl, normalize, same_domain

A, B, C, D = (f"https://site.test/{n}" for n in "abcd")


def run(web, seed, **kwargs):
    calls = []
    result = crawl(seed, fetch_fn=make_fetch(web, calls), **kwargs)
    return result, [url for url, _ in calls]


def test_depth_one_follows_only_links_on_the_seed():
    web = {A: make_page(A, links=[B, C]), B: make_page(B, links=[D]), C: make_page(C), D: make_page(D)}
    result, fetched = run(web, web[A], depth=1)
    assert [p.url for p in result.pages] == [A, B, C]
    assert D not in fetched


def test_depth_two_reaches_second_level():
    web = {A: make_page(A, links=[B]), B: make_page(B, links=[D]), D: make_page(D)}
    result, _ = run(web, web[A], depth=2)
    assert [p.url for p in result.pages] == [A, B, D]


def test_depth_zero_returns_only_the_seed():
    web = {A: make_page(A, links=[B]), B: make_page(B)}
    result, fetched = run(web, web[A], depth=0)
    assert [p.url for p in result.pages] == [A]
    assert fetched == []


def test_max_pages_counts_the_seed():
    links = [f"https://site.test/p{n}" for n in range(30)]
    web = {A: make_page(A, links=links), **{u: make_page(u) for u in links}}
    result, fetched = run(web, web[A], depth=1, max_pages=5)
    assert len(result.pages) == 5
    assert len(fetched) == 4


def test_off_domain_skipped_and_www_counts_as_same_domain():
    web = {
        "https://example.com/": make_page("https://example.com/", links=["https://www.example.com/x", "https://evil.test/y"]),
        "https://www.example.com/x": make_page("https://www.example.com/x"),
    }
    result, fetched = run(web, web["https://example.com/"], depth=1)
    assert fetched == ["https://www.example.com/x"]
    assert len(result.pages) == 2


def test_loops_and_url_variants_are_fetched_once():
    # Review Focus 4
    web = {
        A: make_page(A, links=[A, A + "#top", B, B + "#x", "HTTPS://SITE.TEST/b"]),
        B: make_page(B, links=[A, B]),
    }
    result, fetched = run(web, web[A], depth=3)
    assert fetched == [B]
    assert [p.url for p in result.pages] == [A, B]


def test_redirect_landing_on_a_seen_url_is_not_added_twice():
    web = {A: make_page(A, links=[B]), B: make_page(A)}  # fetching B lands on A
    result, _ = run(web, web[A], depth=1)
    assert [p.url for p in result.pages] == [A]


def test_failed_pages_are_recorded_and_the_crawl_continues():
    web = {A: make_page(A, links=[B, C]), C: make_page(C)}  # B missing -> 404
    result, _ = run(web, web[A], depth=1)
    assert [p.url for p in result.pages] == [A, C]
    assert result.failed == [(B, f"HTTP 404 from {B}")]


def test_cancel_stops_between_pages():
    web = {A: make_page(A, links=[B, C, D]), B: make_page(B), C: make_page(C), D: make_page(D)}
    state = {"n": 0}

    def cancel():
        state["n"] += 1
        return state["n"] > 2  # allow two fetches, then stop

    result, fetched = run(web, web[A], depth=1, should_cancel=cancel)
    assert len(fetched) == 2
    assert [p.url for p in result.pages] == [A, B, C]


def test_progress_and_delay_hooks():
    web = {A: make_page(A, links=[B, C]), B: make_page(B), C: make_page(C)}
    progress, slept = [], []
    crawl(
        web[A],
        fetch_fn=make_fetch(web),
        depth=1,
        max_pages=10,
        delay=0.5,
        on_progress=lambda done, cap: progress.append((done, cap)),
        sleep=slept.append,
    )
    assert progress == [(1, 10), (2, 10), (3, 10)]
    assert slept == [0.5, 0.5]


def test_same_domain_helper():
    assert same_domain("https://a.test/x", "https://A.TEST/y")
    assert not same_domain("https://a.test/", "https://b.test/")


def test_normalize_drops_fragment_and_lowercases_host():
    assert normalize("HTTPS://A.Test") == "https://a.test/"
    assert normalize("https://a.test/p#frag") == "https://a.test/p"


def test_normalize_collapses_trailing_slash_and_default_ports():
    assert normalize("https://a.test/a/") == normalize("https://a.test/a")
    assert normalize("https://a.test:443/a") == "https://a.test/a"
    assert normalize("http://a.test:80/") == "http://a.test/"
    assert normalize("https://a.test:8443/a") == "https://a.test:8443/a"
    assert normalize("https://a.test/a?x=1") == "https://a.test/a?x=1"


def test_trailing_slash_and_default_port_variants_are_fetched_once():
    # Review Focus 4
    web = {A: make_page(A, links=[A + "/", "https://site.test:443/a", B, B + "/"]), B: make_page(B)}
    result, fetched = run(web, web[A], depth=2)
    assert fetched == [B]
    assert result.failed == []
    assert [p.url for p in result.pages] == [A, B]
