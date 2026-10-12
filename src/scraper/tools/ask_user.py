"""ask_user: pause the run and ask the person a question. It has no logic of its own to run: a pause is a LangGraph interrupt."""

from langchain_core.tools import tool
from langgraph.types import interrupt

NAME = "ask_user"
SUMMARY = "Ask you a question when something is unclear, and wait for the answer."  # in plain words, for the person and for the Ask-mode prompt


def make_tool(call=None):
    @tool(response_format="content_and_artifact", parse_docstring=True)
    def ask_user(question: str, options: list[str] | None = None) -> tuple[str, dict]:
        """Ask the human a question and wait for the answer. Use it when the instruction is ambiguous, a login or CAPTCHA blocks the page, every fetch mode failed, or a choice changes the outcome.

        Args:
            question: A short, specific question.
            options: Optional answers to offer as buttons.
        """
        answer = interrupt({"kind": "ask", "question": question, "options": list(options or [])})
        return f"The user answered: {answer}", {"ok": True, "rows_added": 0, "fetched": [], "limit": None}

    return ask_user
