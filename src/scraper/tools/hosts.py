"""Which sites a piece of text names. The agent may fetch only sites the person named."""

import re
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+", re.IGNORECASE)
BARE_RE = re.compile(
    r"(?<![\w@/.-])((?:[a-z0-9-]+\.)+[a-z]{2,24}|localhost|(?:\d{1,3}\.){3}\d{1,3})(?=\.?(?:[:/\s,;)\]\"']|$))",
    re.IGNORECASE,
)


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def hosts_in(text: str) -> set[str]:
    hosts = {host_of(match) for match in URL_RE.findall(text)}
    hosts |= {match.lower().removeprefix("www.") for match in BARE_RE.findall(text)}
    hosts.discard("")
    return hosts
