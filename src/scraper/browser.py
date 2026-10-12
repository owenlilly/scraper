"""Acting on a page in a real browser (Playwright, through Scrapling): fill in a form, click, scroll, and see the requests a page makes.

Each call opens a fresh headless browser, does its one job, and closes it. Nothing carries over from one call to the next, and the model
never gets Playwright itself, only these four narrow actions."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from . import debuglog
from .fetch import FetchError, Page, http_error, page_from, translate_error, validate_url

ACTION_TIMEOUT_MS = 5000
MAX_CLICKS = 10
MAX_SCROLLS = 20
MAX_NETWORK_REQUESTS = 100
PREVIEW_CHARS = 400
PREVIEWS_KEPT = 8  # only this many response bodies are read: the model needs to see a few, not all
# A CSS selector rather than plain words (a field's name, or the text on a button): it starts like one, has combinators or brackets,
# or is a tag with a class, id, attribute or pseudo-class. "Load more" and "Next page" are text.
_CSS_LIKE = re.compile(r"^[#.\[*]|[>\[\]+~]|^[a-z][\w-]*[.#:\[]|\s[#.\[>]")
_TRUE = ("true", "yes", "on", "1", "checked")
_PLAIN_SCHEMES = ("data", "blob", "about")  # never leave the browser, so there is nothing to guard


# ---- finding things on the page ----------------------------------------------------------------------------------------------------------


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def field_selector(key: str) -> str:
    """A CSS selector as it is; anything else is a field's name or id."""
    return key if _CSS_LIKE.search(key) else f"[name={_quote(key)}], [id={_quote(key)}]"


def button_selector(label: str) -> str:
    """A CSS selector as it is; anything else is the text on a button."""
    if _CSS_LIKE.search(label):
        return label
    text = _quote(label)
    return f"button:has-text({text}), input[type=submit][value={text}], input[type=button][value={text}], [role=button]:has-text({text})"


def click_selector(label: str) -> str:
    """A CSS selector as it is; anything else is the text on a button, a link or a tab."""
    if _CSS_LIKE.search(label):
        return label
    text = _quote(label)
    return f"{button_selector(label)}, a:has-text({text}), [role=link]:has-text({text}), [role=tab]:has-text({text})"


def _find(page, selector: str, what: str, hint: str):
    locator = page.locator(selector).first
    try:
        locator.wait_for(state="attached", timeout=ACTION_TIMEOUT_MS)
    except Exception:
        raise FetchError(f"Could not find {what} on the page.", hint=hint) from None
    return locator


# ---- the one place a browser is opened ---------------------------------------------------------------------------------------------------


def _dynamic_with_actions(url: str, timeout: float, setup, action):
    from scrapling.fetchers import DynamicFetcher

    # retries=1: a page that cannot be acted on will not work on a second go, and each go opens a browser.
    return DynamicFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout * 1000, retries=1, page_setup=setup, page_action=action)


def browse(
    url: str,
    *,
    timeout: float,
    allow_request: Callable[[str, bool], bool],
    action: Callable | None = None,
    watch: Callable | None = None,
    backend: Callable | None = None,
    label: str = "browser",
):
    """Open `url`, run `action(page, settle)` on it, and return the browser's response. `settle()` waits until the page has stopped
    loading things (Playwright's own "network idle" returns at once when it has already happened once, so it cannot wait for what a click
    sets off).

    `allow_request(url, is_navigation)` is asked about every request the browser makes. A request it refuses is aborted, so a page that
    sends the browser to a site that is not allowed goes nowhere, and the caller is told. `watch(page)` runs before the page loads, to
    listen for events."""
    url = validate_url(url)
    blocked: list[str] = []
    refused_requests: list[str] = []
    failures: list[Exception] = []
    in_flight = 0
    ms = debuglog.stopwatch()

    def settle_on(page) -> Callable[[], None]:
        def settle() -> None:
            quiet, waited = 0, 0
            while quiet < 3 and waited < timeout * 1000:  # three quiet moments in a row
                page.wait_for_timeout(150)
                waited += 150
                quiet = quiet + 1 if in_flight <= 0 else 0

        return settle

    def setup(page) -> None:
        def count(change: int):
            def on_event(_request) -> None:
                nonlocal in_flight
                in_flight += change

            return on_event

        page.on("request", count(1))
        page.on("requestfinished", count(-1))
        page.on("requestfailed", count(-1))

        def handle(route) -> None:
            request = route.request
            target = request.url
            scheme = urlparse(target).scheme
            if scheme in _PLAIN_SCHEMES:
                route.continue_()
                return
            try:
                navigation = request.is_navigation_request() and request.frame == page.main_frame
            except Exception:
                navigation = False
            if scheme in ("http", "https") and allow_request(target, navigation):
                route.continue_()
                return
            if navigation:
                blocked.append(target)
            refused_requests.append(target)
            debuglog.event("browser.blocked", action=label, url=target, navigation=navigation)
            route.abort()

        page.route("**/*", handle)
        if watch is not None:
            watch(page)

    def act(page) -> None:
        # Scrapling logs an error raised here and carries on with whatever page it has, so keep the error and raise it afterwards.
        try:
            settle = settle_on(page)
            if action is not None:
                action(page, settle)
            settle()
        except Exception as exc:
            failures.append(exc)

    def refused() -> FetchError:
        return FetchError(
            f"The page tried to go to {blocked[0]}, which is not allowed, so it was not opened.",
            hint="Only sites the person named or approved can be opened.",
        )

    def fail(error: FetchError):
        debuglog.event("browser.error", action=label, url=url, ms=ms(), error=error.message, blocked=blocked, refused=len(refused_requests))
        return error

    try:
        response = (backend or _dynamic_with_actions)(url, timeout, setup, act)
    except FetchError as exc:
        raise fail(exc)
    except Exception as exc:
        if blocked:
            raise fail(refused()) from exc
        raise fail(translate_error(exc, "dynamic", url)) from exc
    if blocked:
        raise fail(refused())
    if failures:
        error = failures[0]
        raise fail(error if isinstance(error, FetchError) else translate_error(error, "dynamic", url))
    status = int(response.status)
    if status >= 400:
        raise fail(http_error(status, url))
    debuglog.event(
        "browser.run", action=label, url=url, ms=ms(), status=status, final_url=str(getattr(response, "url", None) or url), blocked=blocked,
        refused=len(refused_requests), preview=debuglog.preview(str(getattr(response, "html_content", ""))),
    )
    return response


# ---- fill in a form ----------------------------------------------------------------------------------------------------------------------


def _fill(page, fields: dict):
    last = None
    for key, value in fields.items():
        field = _find(page, field_selector(key), f"the field {key!r}", "Use a field name listed with the page, or a CSS selector such as '#email'.")
        kind = field.evaluate("el => el.tagName.toLowerCase() + ':' + (el.type || '')")
        if kind.startswith("select"):
            field.select_option(str(value), timeout=ACTION_TIMEOUT_MS)
        elif kind in ("input:checkbox", "input:radio"):
            truthy = value if isinstance(value, bool) else str(value).strip().lower() in _TRUE
            field.set_checked(truthy, timeout=ACTION_TIMEOUT_MS)
        else:
            field.fill(str(value), timeout=ACTION_TIMEOUT_MS)
        last = field
    return last


def submit_form(
    url: str,
    fields: dict,
    submit: str | None = None,
    *,
    timeout: float = 20.0,
    allow_request: Callable[[str, bool], bool],
    backend: Callable | None = None,
) -> Page:
    """Fill in `fields` (name, id or CSS selector -> value), press `submit` (CSS selector or the button's text; Enter in the last field
    when none), and return the page that results."""

    def action(page, settle) -> None:
        last = _fill(page, fields)
        if submit:
            button = _find(page, button_selector(submit), f"the button {submit!r}", "Use the button's text or a CSS selector such as '#go'.")
            button.click(timeout=ACTION_TIMEOUT_MS)
        elif last is not None:
            last.press("Enter", timeout=ACTION_TIMEOUT_MS)

    url = validate_url(url)
    return page_from(browse(url, timeout=timeout, allow_request=allow_request, action=action, backend=backend, label="browser_submit_form"), url)


# ---- click -------------------------------------------------------------------------------------------------------------------------------


def click_element(
    url: str,
    target: str,
    times: int = 1,
    *,
    timeout: float = 20.0,
    allow_request: Callable[[str, bool], bool],
    backend: Callable | None = None,
) -> Page:
    """Click a button, link or tab (its text, or a CSS selector) up to `times` times, as for "Load more", and return the page afterwards.
    Clicking stops early once there is nothing left to click."""
    selector = click_selector(target)

    def action(page, settle) -> None:
        _find(page, selector, f"the element {target!r}", "Use the text on the button or link, or a CSS selector such as '#load-more'.")
        for count in range(max(1, min(times, MAX_CLICKS))):
            if count and page.locator(selector).count() == 0:
                break  # it is gone, so everything has been loaded
            locator = page.locator(selector).first
            locator.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
            locator.click(timeout=ACTION_TIMEOUT_MS)
            settle()

    url = validate_url(url)
    return page_from(browse(url, timeout=timeout, allow_request=allow_request, action=action, backend=backend, label="browser_click"), url)


# ---- scroll ------------------------------------------------------------------------------------------------------------------------------


def scroll_page(
    url: str,
    times: int = 3,
    *,
    timeout: float = 20.0,
    allow_request: Callable[[str, bool], bool],
    backend: Callable | None = None,
) -> Page:
    """Scroll to the bottom up to `times` times, so content that loads on scroll appears, and return the page afterwards. Scrolling stops
    early once the page stops growing."""

    def action(page, settle) -> None:
        height = page.evaluate("document.body.scrollHeight")
        for _ in range(max(1, min(times, MAX_SCROLLS))):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            settle()
            grown = page.evaluate("document.body.scrollHeight")
            if grown <= height:
                break
            height = grown

    url = validate_url(url)
    return page_from(browse(url, timeout=timeout, allow_request=allow_request, action=action, backend=backend, label="browser_scroll"), url)


# ---- network -----------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NetworkRequest:
    method: str
    url: str
    status: int
    content_type: str
    size: int
    preview: str = ""  # the start of a JSON or text response, for the first few


def network_requests(
    url: str,
    *,
    timeout: float = 20.0,
    allow_request: Callable[[str, bool], bool],
    contains: str | None = None,
    limit: int = 30,
    backend: Callable | None = None,
) -> list[NetworkRequest]:
    """The background requests (XHR and fetch calls) a page makes while it loads, such as the JSON that fills a list. `contains` keeps
    only those with that text in the address."""
    seen: list = []
    found: list[NetworkRequest] = []
    wanted = max(1, min(limit, MAX_NETWORK_REQUESTS))
    previews = 0

    def watch(page) -> None:
        page.on("response", lambda response: seen.append(response) if response.request.resource_type in ("xhr", "fetch") else None)

    def action(page, settle) -> None:
        nonlocal previews
        settle()
        for response in seen[:MAX_NETWORK_REQUESTS]:
            if len(found) >= wanted:
                break  # the rest would be cut off below, so do not read their bodies
            if contains and contains.lower() not in response.url.lower():
                continue
            content_type = (response.headers.get("content-type") or "").split(";")[0]
            try:
                body = response.body()
            except Exception:
                body = b""
            readable = any(token in content_type for token in ("json", "text", "xml"))
            preview = ""
            if readable and previews < PREVIEWS_KEPT:
                preview = body[:PREVIEW_CHARS].decode("utf-8", "replace")
            previews += bool(preview)
            found.append(NetworkRequest(response.request.method, response.url, response.status, content_type, len(body), preview))

    url = validate_url(url)
    browse(url, timeout=timeout, allow_request=allow_request, action=action, watch=watch, backend=backend, label="browser_network")
    return found


# ---- the set the agent is given ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Browser:
    """The browser actions a session may use. Hosted sessions get a wrapped set (see hosted.make_hosted_browser)."""

    submit_form: Callable
    click: Callable
    scroll: Callable
    network: Callable


DEFAULT_BROWSER = Browser(submit_form=submit_form, click=click_element, scroll=scroll_page, network=network_requests)
