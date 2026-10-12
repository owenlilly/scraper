"""Hosted mode: refuse URLs that would make the server call something that is not on the public internet."""

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from .crawl import DEFAULT_PORTS
from .fetch import FetchError

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
_NUMBER = r"(?:0[xX][0-9a-fA-F]*|[0-9]+)"  # one label written as decimal, octal or hex
_NUMERIC_HOST = re.compile(rf"^{_NUMBER}(?:\.{_NUMBER})*\.?$")
_ODD_CHARACTERS = re.compile(r"[\x00-\x20\x7f\\]")  # control characters, spaces and backslashes: parsers disagree on these


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 zone id such as fe80::1%en0
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_public_url(url: str, *, resolver: Callable = socket.getaddrinfo, ports: frozenset[int] = ALLOWED_PORTS) -> None:
    """Raise FetchError unless `url` is http(s), has no credentials, uses an allowed port and resolves only to public addresses.

    This closes the direct cases. A hostname can still answer differently between this check and the connection
    (DNS rebinding), so the host must also block private ranges at the network level."""
    if _ODD_CHARACTERS.search(url):
        raise FetchError("Enter a valid URL")
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise FetchError("Enter a valid URL") from None
    if parts.scheme not in DEFAULT_PORTS:
        raise FetchError("Only http and https URLs are supported")
    if parts.username is not None or parts.password is not None:
        raise FetchError("URLs with credentials are not allowed")
    if not host:
        raise FetchError("Enter a valid URL")
    if not host.isascii():
        raise FetchError("Use the punycode form of this address (xn--...)")
    if _NUMERIC_HOST.match(host):  # 2130706433, 0x7f.1, 0177.0.0.1: the resolver and curl can read these differently
        try:
            ipaddress.IPv4Address(host)  # only plain dotted-decimal, such as 93.184.216.34, is accepted
        except ValueError:
            raise FetchError(f"{host} is not a plain address. Write IP addresses as four decimal numbers.") from None
    port = port or DEFAULT_PORTS[parts.scheme]
    if port not in ports:
        raise FetchError(f"Port {port} is not allowed. Use one of: {', '.join(str(p) for p in sorted(ports))}.")
    try:
        answers = resolver(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise FetchError(f"Could not find {host}") from None
    addresses = [answer[4][0] for answer in answers]
    if not addresses:
        raise FetchError(f"Could not find {host}")
    if not all(_is_public(address) for address in addresses):
        raise FetchError(f"{host} is not a public address, so it cannot be fetched")
