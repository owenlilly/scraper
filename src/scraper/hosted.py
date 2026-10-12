"""Hosted mode: the fetch function every user's session gets. Restricts modes, blocks private addresses, counts pages."""

from collections.abc import Callable
from dataclasses import replace

from . import fetch as fetch_mod
from .config import Config
from .netguard import check_public_url
from .usage import UsageMeter


CUT_NOTICE = "\n\n[The rest of this page was cut off to save space.]"


def _cut(text: str, limit: int, notice: str = "") -> str:
    """At most `limit` characters; when something is removed, `notice` (if it fits) says so."""
    if len(text) <= limit:
        return text
    if len(notice) >= limit:
        return text[:limit]
    return text[: limit - len(notice)] + notice


def make_hosted_fetch(
    cfg: Config,
    meter: UsageMeter,
    *,
    base: Callable[..., fetch_mod.Page] = fetch_mod.fetch,
    check: Callable[[str], None] = check_public_url,
) -> Callable[..., fetch_mod.Page]:
    modes = cfg.allowed_modes
    limit = cfg.max_page_chars

    def hosted_fetch(url: str, mode: str = "fast", *, timeout: float = 20.0) -> fetch_mod.Page:
        if mode not in modes:
            raise fetch_mod.FetchError(f"The {mode} mode is not available on this server. Use: {', '.join(modes)}.")
        url = fetch_mod.validate_url(url)
        check(url)
        meter.charge_page()  # only a fetch that will really happen costs allowance
        try:
            page = base(url, mode, timeout=timeout)
            # Cut here, before the session keeps the page: otherwise a huge page stays in memory, is served by the preview
            # route and is read by the model in full, and only its stored copy would be short.
            return replace(page, markdown=_cut(page.markdown, limit, CUT_NOTICE), html=_cut(page.html, limit))
        except fetch_mod.FetchError as exc:
            if exc.retry_mode and exc.retry_mode not in modes:  # do not send the model to a mode it cannot use
                raise fetch_mod.FetchError(exc.message) from exc
            raise

    return hosted_fetch
