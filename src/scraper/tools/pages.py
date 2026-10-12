"""What the tools that open a page (fetch_page and the browser tools) share: the allowlist check, the budget, and how a page is shown to the model."""

from collections.abc import Callable

from scrapling.parser import Selector

from ..context import estimate_tokens, view_page
from ..fetch import FetchError, Page, validate_url
from .hosts import host_of
from .result import ToolResult

MAX_FORMS_SHOWN = 5
_NOT_FIELDS = {"hidden", "submit", "button", "image", "reset"}


def not_allowed(box, host: str) -> ToolResult:
    allowed = ", ".join(sorted(box.allowed_hosts)) or "none"
    return box.error(
        f"Fetching {host} is not allowed. Allowed sites: {allowed}. "
        "The user must name a site in their instruction or approve it before it can be fetched; say so in your answer if you need it."
    )


def allow_host(box, host: str) -> ToolResult | None:
    """None when the site may be opened (asking the person about a new one), otherwise the error to hand back."""
    if host in box.allowed_hosts:
        return None
    if box.approve_host is not None and box.approve_host(host):
        box.allowed_hosts.add(host)
        return None
    return not_allowed(box, host)


def describe_forms(page: Page) -> str:
    """The forms on a page, with the names of their fields, so the model can fill one in with browser_submit_form. (The page's text leaves forms out.)"""
    if not page.html:
        return ""
    lines = []
    for form in Selector(page.html, url=page.url).css("form"):
        names: list[str] = []
        for element in form.css("input, select, textarea"):
            if (element.attrib.get("type") or "text").lower() in _NOT_FIELDS:
                continue
            name = element.attrib.get("name") or element.attrib.get("id")
            if name and name not in names:
                names.append(name)
        if not names:
            continue
        ident = f"#{form.attrib['id']}" if form.attrib.get("id") else "form"
        method = (form.attrib.get("method") or "get").upper()
        action = form.attrib.get("action") or "this page"
        label = ""
        for button in form.css("button, input[type=submit]"):
            text = button.attrib.get("value") if button.tag == "input" else " ".join(button.get_all_text().split())
            if text:
                label = f'; button "{text}"'
                break
        lines.append(f"- {ident} ({method} {action}): fields {', '.join(names)}{label}")
    if not lines:
        return ""
    return "Forms on this page (fill one in with browser_submit_form):\n" + "\n".join(lines[:MAX_FORMS_SHOWN]) + "\n"


def page_size(page: Page) -> str:
    """How big a page is, in the words the tools open with: "about 1200 tokens, 34 links"."""
    return f"about {estimate_tokens(page.markdown)} tokens, {len(page.links)} links"


def page_result(box, page: Page, header: str, focus: str | None) -> ToolResult:
    forms = describe_forms(page)
    text_header = header + forms
    body = view_page(page, focus, max(200, box.result_tokens - estimate_tokens(text_header) - 10))
    return box.ok(text_header + body, fetched=(page.url,))


# ---- the browser tools -----------------------------------------------------------------------------------------------------------------


def open_site(box, raw_url: str) -> tuple[str | None, ToolResult | None]:
    """Check that a site may be opened (asking the person about a new one) and that there is page budget left, then take a page of it and
    keep to the fetch pause. Returns the address to open, or the error to hand back."""
    try:
        url = validate_url(raw_url)
    except FetchError as exc:
        return None, box.error(exc.message)
    refused = allow_host(box, host_of(url))
    if refused is not None:
        return None, refused
    if box.fetched >= box.max_pages:
        return None, box.error(
            f"The page budget of {box.max_pages} fetches is used up. Stop fetching and answer with what you have.",
            limit="pages",
        )
    box.fetched += 1
    box.pace()
    return url, None


def may_open(box) -> Callable[[str, bool], bool]:
    """The rule the browser works under: pages load their own scripts and images from anywhere public, but only moving to another page
    must stay on an allowed site. Looked up when asked, so a site approved during the call counts."""
    return lambda target, navigation: not navigation or host_of(target) in box.allowed_hosts


def run_browser(box, raw_url: str, call: Callable[[str, Callable], object], present: Callable[[str, object], ToolResult]) -> ToolResult:
    """The shared shape of a browser tool: open_site, run `call(url, allow_request)` against the browser, and `present(url, outcome)`."""
    url, error = open_site(box, raw_url)
    if error is not None:
        return error
    try:
        outcome = call(url, may_open(box))
    except FetchError as exc:
        return box.error(exc.text)
    finally:
        box.last_fetch = box.clock()
    return present(url, outcome)


def show_page(box, page: Page, header: str) -> ToolResult:
    """A page a browser tool came back with: discarded if it is on a site that is not allowed, otherwise kept and shown."""
    if host_of(page.url) not in box.allowed_hosts:
        return box.error(
            f"That led to {page.url}, which is not an allowed site, so the result was discarded. "
            f"Allowed sites: {', '.join(sorted(box.allowed_hosts))}."
        )
    box.store(page)
    if box.on_page:
        box.on_page(page)
    return page_result(box, page, header, None)
