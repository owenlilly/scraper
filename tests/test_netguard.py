import socket

import pytest

from scraper.fetch import FetchError
from scraper.netguard import check_public_url


def resolver_for(*addresses):
    """A getaddrinfo stand-in that answers every lookup with the given addresses."""

    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    return resolve


PUBLIC = resolver_for("93.184.216.34")


@pytest.mark.parametrize("url", ["https://example.test/", "http://example.test/a?b=1", "https://example.test:8443/x", "http://example.test:8080/"])
def test_public_urls_on_allowed_ports_pass(url):
    check_public_url(url, resolver=PUBLIC)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254",  # loopback, private, cloud metadata
        "100.64.0.1",  # carrier-grade NAT (Tailscale and friends)
        "0.0.0.0", "224.0.0.1", "240.0.0.1",
        "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",  # IPv6 loopback, link-local, unique-local, IPv4-mapped
    ],
)
def test_non_public_addresses_are_rejected(address):
    with pytest.raises(FetchError, match="not a public address"):
        check_public_url("https://example.test/", resolver=resolver_for(address))


def test_any_non_public_answer_rejects_the_host_even_when_others_are_public():
    with pytest.raises(FetchError, match="not a public address"):
        check_public_url("https://example.test/", resolver=resolver_for("93.184.216.34", "10.0.0.5"))


@pytest.mark.parametrize("url", ["ftp://example.test/", "file:///etc/passwd", "gopher://example.test/", "javascript:alert(1)"])
def test_only_http_and_https_are_allowed(url):
    with pytest.raises(FetchError, match="Only http and https"):
        check_public_url(url, resolver=PUBLIC)


@pytest.mark.parametrize("url", ["https://user:pw@example.test/", "https://user@example.test/", "http://example.test@evil.test/"])
def test_urls_with_credentials_are_rejected(url):
    with pytest.raises(FetchError, match="credentials"):
        check_public_url(url, resolver=PUBLIC)


@pytest.mark.parametrize("url", ["http://example.test:22/", "https://example.test:5432/", "http://example.test:6379/", "http://example.test:9200/"])
def test_unusual_ports_are_rejected(url):
    with pytest.raises(FetchError, match="Port"):
        check_public_url(url, resolver=PUBLIC)


def test_unresolvable_hosts_are_rejected_with_a_plain_message():
    def broken(*args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    with pytest.raises(FetchError, match="Could not find"):
        check_public_url("https://nope.invalid/", resolver=broken)


def test_a_host_that_resolves_to_nothing_is_rejected():
    with pytest.raises(FetchError, match="Could not find"):
        check_public_url("https://example.test/", resolver=lambda *a, **k: [])


@pytest.mark.parametrize("url", ["http://", "https:///path", "https://:443/"])
def test_urls_without_a_host_are_rejected(url):
    with pytest.raises(FetchError):
        check_public_url(url, resolver=PUBLIC)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/", "http://LOCALHOST:8080/", "http://localhost./", "http://127.0.0.1/", "http://[::1]/",
        "http://2130706433/", "http://0x7f.1/", "http://0177.0.0.1/", "http://127.1/", "http://0/",
        "http://169.254.169.254/latest/meta-data/", "http://[::ffff:127.0.0.1]/",
    ],
)
def test_loopback_in_every_spelling_is_rejected_using_the_real_resolver(url):
    with pytest.raises(FetchError):
        check_public_url(url)


def test_the_error_names_the_host_but_never_the_resolved_address():
    with pytest.raises(FetchError) as caught:
        check_public_url("https://internal.example.test/", resolver=resolver_for("10.1.2.3"))
    assert "internal.example.test" in caught.value.message
    assert "10.1.2.3" not in caught.value.message


@pytest.mark.parametrize("host", ["0177.0.0.1", "0x7f.0.0.1", "2130706433", "127.1", "0x7f000001", "0300.0250.0.1", "1.2.3"])
def test_ambiguous_numeric_hosts_are_rejected_even_when_the_resolver_calls_them_public(host):
    # macOS reads 0177.0.0.1 as 177.0.0.1 while curl reads it as 127.0.0.1, so the guard must not rely on the resolver here
    with pytest.raises(FetchError, match="plain address"):
        check_public_url(f"http://{host}/", resolver=PUBLIC)


def test_a_plain_public_ip_literal_is_accepted():
    check_public_url("https://93.184.216.34/", resolver=resolver_for("93.184.216.34"))


@pytest.mark.parametrize("url", ["http://example.test\\@127.0.0.1/", "http://exam ple.test/", "http://example.test/\x00", "http://example.test/\nHost: x"])
def test_backslashes_spaces_and_control_characters_are_rejected(url):
    with pytest.raises(FetchError, match="valid URL"):
        check_public_url(url, resolver=PUBLIC)


def test_non_ascii_hosts_must_be_written_as_punycode():
    with pytest.raises(FetchError, match="punycode"):
        check_public_url("https://exämple.test/", resolver=PUBLIC)
    check_public_url("https://xn--exmple-cua.test/", resolver=PUBLIC)


def test_hosts_that_merely_look_hexadecimal_are_ordinary_names():
    check_public_url("https://deadbeef.cafe/", resolver=PUBLIC)
    check_public_url("https://123abc.test/", resolver=PUBLIC)
