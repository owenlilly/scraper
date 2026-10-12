"""add_rows: add rows to the results table the person can download."""

from langchain_core.tools import tool

from .result import ToolArgError, ToolResult

NAME = "add_rows"
SUMMARY = "Add rows to the results table you can download."  # in plain words, for the person and for the Ask-mode prompt
MAX_ROWS_PER_CALL = 200


def run(box, args: dict) -> ToolResult:
    rows = args.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ToolArgError("'rows' must be a non-empty list of objects.")
    if len(rows) > MAX_ROWS_PER_CALL:
        raise ToolArgError(f"Add at most {MAX_ROWS_PER_CALL} rows per call.")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ToolArgError(f'Row {index} is not an object. Every row must be an object like {{"name": "...", "price": "..."}}.')
    added: list[dict] = []
    for row in rows:
        row = dict(row)
        if row not in box.rows and row not in added:
            added.append(row)
    box.rows.extend(added)
    if added and box.on_rows:
        box.on_rows(added)
    columns = list(dict.fromkeys(key for row in box.rows for key in row))
    skipped = len(rows) - len(added)
    note = f" ({skipped} duplicate rows ignored)" if skipped else ""
    return box.ok(
        f"Added {len(added)} rows{note}. The table now has {len(box.rows)} rows with columns: {', '.join(columns)}.",
        rows_added=len(added),
    )


def make_tool(call):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def add_rows(rows: list[dict]) -> tuple[str, dict]:
        """Add rows to the results table the user can download. One object per item, with consistent keys. Call it as you go.

        Args:
            rows: Objects such as [{"name": "Starter", "price": "$10"}].
        """
        return call(NAME, {"rows": rows})

    return add_rows
