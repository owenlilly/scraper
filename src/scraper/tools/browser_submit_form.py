"""browser_submit_form: type into a form on a page and press its button, in a real browser, and return the page that comes back."""

from langchain_core.tools import tool

from .pages import page_size, run_browser, show_page
from .result import ToolArgError, ToolResult, str_arg

NAME = "browser_submit_form"
SUMMARY = "Use a real browser to type into a form, such as a search box, and press its button."  # in plain words, for the person and for the Ask-mode prompt
MAX_FIELDS = 20


def _read_fields(args: dict) -> dict:
    fields = args.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ToolArgError("Argument 'fields' must be an object of field name to value, such as {\"q\": \"running shoes\"}.")
    if len(fields) > MAX_FIELDS:
        raise ToolArgError(f"Fill in at most {MAX_FIELDS} fields at once.")
    for key, value in fields.items():
        if not isinstance(value, (str, int, float, bool)):
            raise ToolArgError(f"The value for field '{key}' must be text, a number, or true or false.")
    return fields


def run(box, args: dict) -> ToolResult:
    url = str_arg(args, "url", required=True)
    fields = _read_fields(args)
    submit = str_arg(args, "submit")

    def present(opened: str, page) -> ToolResult:
        header = f"Submitted the form on {opened}. The result is {page.url}: {page_size(page)}.\n"
        return show_page(box, page, header)

    return run_browser(box, url, lambda opened, allow: box.browser.submit_form(opened, fields, submit, timeout=box.timeout, allow_request=allow), present)


def make_tool(call):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def browser_submit_form(url: str, fields: dict[str, str | bool], submit: str | None = None) -> tuple[str, dict]:
        """Use a real browser (Playwright) to type into a form on a web page and click its button, then return the page that comes back. Use it for a search box, a filter form or any form that fetch_page alone cannot use. fetch_page lists the forms it finds, with their field names.

        Args:
            url: The page that has the form.
            fields: Field name (or id, or a CSS selector such as '#email') to value. Use true or false for a checkbox, and an option's value or label for a menu.
            submit: The button to press: its text, such as 'Search', or a CSS selector. Leave it out to press Enter in the last field.
        """
        return call(NAME, {"url": url, "fields": fields, "submit": submit})

    return browser_submit_form
