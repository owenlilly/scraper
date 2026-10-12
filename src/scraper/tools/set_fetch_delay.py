"""set_fetch_delay: let the model slow its own fetching down when a site pushes back."""

from langchain_core.tools import tool

from .result import ToolResult, seconds_arg

NAME = "set_fetch_delay"
SUMMARY = "Slow its own fetching down when a site pushes back."  # in plain words, for the person and for the Ask-mode prompt
MAX_FETCH_DELAY = 30.0


def run(box, args: dict) -> ToolResult:
    wanted = seconds_arg(args, "seconds")
    box.delay = min(box.max_delay, max(box.min_delay, wanted))
    if box.on_delay:
        box.on_delay(box.delay)
    text = f"Fetch delay is now {box.delay:g}s between requests."
    if wanted < box.min_delay:
        text += f" That is the minimum allowed; {wanted:g}s is too fast."
    elif wanted > box.max_delay:
        text += f" That is the maximum allowed; {wanted:g}s is too slow."
    return box.ok(text)


def make_tool(call):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def set_fetch_delay(seconds: float, reason: str | None = None) -> tuple[str, dict]:
        """Set the pause between page fetches. Raise it when a site answers 429, times out or blocks you; it cannot go below the user's minimum.

        Args:
            seconds: Seconds to wait between requests, for example 2.
            reason: Short note on why, shown to the user.
        """
        return call(NAME, {"seconds": seconds, "reason": reason})

    return set_fetch_delay
