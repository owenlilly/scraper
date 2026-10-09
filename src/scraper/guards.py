"""Agent middleware: run limits that pause for the human, repeat guard, context trimming, wrap-up."""

import json
from collections.abc import Callable

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import interrupt
from typing_extensions import NotRequired

from .context import estimate_tokens

REPEAT_LIMIT = 3
KEEP_RECENT_TOOL_RESULTS = 2
TRIMMED = "[trimmed to save context]"
LIMIT_ACTIONS = [
    {"label": "Continue", "action": "continue"},
    {"label": "Wrap up and answer", "action": "wrap_up"},
    {"label": "Stop", "action": "stop"},
]
WRAP_UP_TEXT = "Limit reached. Do not call any more tools. Reply now with your final answer based on what you have collected."
CONTINUE_TEXT = {
    "steps": "The user wants you to keep going.",
    "pages": "The user granted more page fetches. Keep going.",
    "repeat": "The user says to continue, but change your approach: you were repeating the same call.",
}


class GuardState(AgentState):
    steps: NotRequired[int]
    allowance: NotRequired[int]
    wrap_up: NotRequired[bool]
    last_sig: NotRequired[str]
    streak: NotRequired[int]
    end_reason: NotRequired[str]


def _text(message) -> str:
    content = message.content
    return content if isinstance(content, str) else json.dumps(content)


def message_tokens(messages) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(_text(message))
        for tool_call in getattr(message, "tool_calls", None) or []:
            total += estimate_tokens(json.dumps(tool_call.get("args", {})))
    return total


class ContextTrim(AgentMiddleware):
    state_schema = GuardState

    def __init__(self, max_tokens: int) -> None:
        self.max_tokens = max_tokens

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        messages = state["messages"]
        if message_tokens(messages) <= self.max_tokens:
            return None
        working = list(messages)
        indexes = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage) and m.content != TRIMMED]
        updates = []
        for i in indexes[: max(0, len(indexes) - KEEP_RECENT_TOOL_RESULTS)]:
            old = messages[i]
            stub = ToolMessage(content=TRIMMED, tool_call_id=old.tool_call_id, id=old.id, name=old.name, artifact=old.artifact)
            working[i] = stub
            updates.append(stub)
            if message_tokens(working) <= self.max_tokens:
                break
        if message_tokens(working) > self.max_tokens:
            return {"end_reason": "context", "jump_to": "end", "messages": updates}
        return {"messages": updates} if updates else None

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)


class RunGuard(AgentMiddleware):
    state_schema = GuardState

    def __init__(self, *, max_steps: int, on_grant: Callable[[str], None] | None = None) -> None:
        self.max_steps = max_steps
        self.on_grant = on_grant

    @staticmethod
    def _latest_call_signature(messages) -> str | None:
        """Signature of the tool calls the tools node has just answered, else None."""
        if not messages or not isinstance(messages[-1], ToolMessage):
            return None
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                calls = message.tool_calls or []
                if not calls:
                    return None
                return json.dumps(sorted((c["name"], json.dumps(c["args"], sort_keys=True, default=str)) for c in calls))
        return None

    @staticmethod
    def _pages_exhausted(messages) -> bool:
        for message in reversed(messages):
            if not isinstance(message, ToolMessage):
                break
            artifact = message.artifact if isinstance(message.artifact, dict) else {}
            if artifact.get("limit") == "pages":
                return True
        return False

    @staticmethod
    def _payload(reason: str, steps: int, allowance: int) -> dict:
        questions = {
            "steps": f"I have used {steps} of my {allowance} steps and I am not finished. What should I do?",
            "pages": "I have used up my page-fetch budget. What should I do?",
            "repeat": "I keep making the same call and I am not getting anywhere. What should I do?",
        }
        return {"kind": "limit", "reason": reason, "question": questions[reason], "actions": LIMIT_ACTIONS}

    def _apply(self, decision, reason: str, base: dict, allowance: int) -> dict:
        if not isinstance(decision, dict):
            decision = {"action": str(decision)}
        action = decision.get("action", "stop")
        note = str(decision.get("note") or "").strip()
        suffix = f" The user adds: {note}" if note else ""
        if action == "continue":
            update = dict(base)
            if reason == "steps":
                update["allowance"] = allowance + self.max_steps
            elif reason == "pages" and self.on_grant:
                self.on_grant("pages")
            elif reason == "repeat":
                update["streak"] = 0
            update["messages"] = [HumanMessage(CONTINUE_TEXT[reason] + suffix)]
            return update
        if action == "wrap_up":
            return {**base, "wrap_up": True, "messages": [HumanMessage(WRAP_UP_TEXT + suffix)]}
        return {"end_reason": "stopped", "jump_to": "end"}

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        messages = state["messages"]
        steps = state.get("steps", 0)
        allowance = state.get("allowance", self.max_steps)
        signature = self._latest_call_signature(messages)
        streak, last_sig = state.get("streak", 0), state.get("last_sig")
        if signature is not None:
            streak = streak + 1 if signature == last_sig else 1
            last_sig = signature
        base = {"steps": steps + 1, "streak": streak}
        if last_sig is not None:
            base["last_sig"] = last_sig
        if state.get("wrap_up"):
            return base
        reason = None
        if steps >= allowance:
            reason = "steps"
        elif self._pages_exhausted(messages):
            reason = "pages"
        elif streak >= REPEAT_LIMIT:
            reason = "repeat"
        if reason is None:
            return base
        decision = interrupt(self._payload(reason, steps, allowance))
        return self._apply(decision, reason, base, allowance)

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)

    def wrap_model_call(self, request, handler):
        if request.state.get("wrap_up"):
            request = request.override(tools=[])
        return handler(request)

    async def awrap_model_call(self, request, handler):
        if request.state.get("wrap_up"):
            request = request.override(tools=[])
        return await handler(request)
