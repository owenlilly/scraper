"""Hosted mode: the fetch function every user's session gets. Restricts modes, blocks private addresses, counts pages."""

from collections.abc import Callable
from dataclasses import replace

from . import fetch as fetch_mod
from . import search as search_mod
from .browser import DEFAULT_BROWSER, Browser
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


def _cut_page(page: fetch_mod.Page, limit: int) -> fetch_mod.Page:
    return replace(page, markdown=_cut(page.markdown, limit, CUT_NOTICE), html=_cut(page.html, limit))


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
            return _cut_page(page, limit)
        except fetch_mod.FetchError as exc:
            if exc.retry_mode and exc.retry_mode not in modes:  # do not send the model to a mode it cannot use
                raise fetch_mod.FetchError(exc.message) from exc
            raise

    return hosted_fetch


def make_hosted_browser(
    cfg: Config,
    meter: UsageMeter,
    *,
    base: Browser = DEFAULT_BROWSER,
    check: Callable[[str], None] = check_public_url,
) -> Browser:
    """The browser actions for a hosted session. The page each starts on and every request the browser makes must be on the public
    internet, and each use costs a page of the person's daily allowance. Where the browser may navigate to is decided by the tool."""
    limit = cfg.max_page_chars

    def guarded(action: Callable) -> Callable:
        def hosted(url: str, *args, timeout: float = 20.0, allow_request: Callable[[str, bool], bool], **kwargs):
            url = fetch_mod.validate_url(url)
            check(url)
            meter.charge_page()  # only an action that will really happen costs allowance

            def allow(target: str, navigation: bool) -> bool:
                try:
                    check(target)
                except fetch_mod.FetchError:
                    return False
                return allow_request(target, navigation)

            result = action(url, *args, timeout=timeout, allow_request=allow, **kwargs)
            if isinstance(result, fetch_mod.Page):
                return _cut_page(result, limit)
            return result

        return hosted

    return Browser(
        submit_form=guarded(base.submit_form), click=guarded(base.click), scroll=guarded(base.scroll), network=guarded(base.network)
    )


def make_hosted_search(cfg: Config, meter: UsageMeter, *, base: Callable[..., list] = search_mod.web_search) -> Callable[..., list]:
    """Web search for a hosted session: it uses the deployment's search service and key, and costs a page of the person's daily allowance.
    The address searched is the deployment's own setting, never the person's, so there is nothing to check against private networks."""

    def hosted_search(query: str, *, num_results: int = 5, objective: str | None = None, timeout: float = 20.0) -> list:
        meter.charge_page()
        return base(query, num_results=num_results, objective=objective, timeout=timeout, url=cfg.exa_url, api_key=cfg.exa_api_key)

    return hosted_search
