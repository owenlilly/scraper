"""Build the LangGraph agent and map its streams to UI-facing AgentEvents."""

import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
import openai
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from .guards import ContextTrim, RunGuard


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["step", "reasoning", "answer", "tool_start", "tool_result", "rows_added", "needs_input", "limit", "error", "done"]
    text: str = ""
    name: str = ""
    args: dict = field(default_factory=dict)
    step: int = 0
    ok: bool = True
    count: int = 0
    urls: tuple[str, ...] = ()
    payload: dict = field(default_factory=dict)


async def open_checkpointer(path: Path, stack: AsyncExitStack) -> AsyncSqliteSaver:
    """Open the SQLite checkpointer for the life of `stack`. An unreadable file is set aside and replaced."""
    path = Path(path)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, mode=0o700)
    for attempt_number in (1, 2):
        attempt = AsyncExitStack()
        try:
            saver = await attempt.enter_async_context(AsyncSqliteSaver.from_conn_string(str(path)))
            await saver.setup()
            await saver.aget_tuple({"configurable": {"thread_id": "probe"}})
        except Exception:
            await attempt.aclose()
            if attempt_number == 2 or not path.exists():
                raise
            path.rename(path.with_name(f"{path.name}.corrupt-{int(time.time())}"))
            for suffix in ("-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            continue
        stack.push_async_callback(attempt.aclose)
        return saver


def build_agent(model, tools, *, system_prompt, checkpointer, max_steps, max_context_tokens, on_grant=None):
    return create_agent(
        model,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        middleware=[ContextTrim(max_context_tokens), RunGuard(max_steps=max_steps, on_grant=on_grant)],
    )


def _plain(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))


def describe_error(exc: Exception, base_url: str) -> str:
    if isinstance(exc, openai.APIStatusError):
        return f"LM Studio rejected the request ({exc.status_code}): {exc.message}"
    if isinstance(exc, (openai.APIConnectionError, httpx.HTTPError)):
        return f"Cannot reach LM Studio at {base_url}"
    return f"{type(exc).__name__}: {exc}"


class AgentRunner:
    def __init__(self, agent, *, max_steps: int, base_url: str = "") -> None:
        self.agent = agent
        self.base_url = base_url
        self.recursion_limit = max_steps * 4 + 25

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": self.recursion_limit}

    async def run(self, thread_id: str, messages: list[dict]) -> AsyncIterator[AgentEvent]:
        async for event in self._stream({"messages": messages}, thread_id):
            yield event

    async def resume(self, thread_id: str, value, interrupt_id: str | None = None) -> AsyncIterator[AgentEvent]:
        # With several pauses pending at once LangGraph needs to know which one this answer belongs to.
        resume = {interrupt_id: value} if interrupt_id else value
        async for event in self._stream(Command(resume=resume), thread_id):
            yield event

    async def proceed(self, thread_id: str) -> AsyncIterator[AgentEvent]:
        async for event in self._stream(None, thread_id):
            yield event

    async def pending(self, thread_id: str) -> dict | None:
        state = await self.agent.aget_state(self._config(thread_id))
        for task in state.tasks:
            for item in task.interrupts:
                return {**dict(item.value), "interrupt_id": item.id}
        return None

    async def delete(self, thread_id: str) -> None:
        await self.agent.checkpointer.adelete_thread(thread_id)

    async def _stream(self, payload, thread_id: str) -> AsyncIterator[AgentEvent]:
        config = self._config(thread_id)
        paused = False
        try:
            async for mode, chunk in self.agent.astream(payload, config, stream_mode=["messages", "updates"]):
                for event in self._map(mode, chunk):
                    paused = paused or event.kind == "needs_input"
                    yield event
            if paused:
                return
            yield await self._terminal(config)
        except Exception as exc:  # any failure ends the stream with an event; the thread stays resumable
            yield AgentEvent("error", describe_error(exc, self.base_url))

    def _map(self, mode: str, chunk):
        if mode == "messages":
            message, meta = chunk
            if meta.get("langgraph_node") != "model" or not isinstance(message, AIMessageChunk):
                return
            reasoning = (message.additional_kwargs or {}).get("reasoning_content")
            if reasoning:
                yield AgentEvent("reasoning", reasoning)
            if isinstance(message.content, str) and message.content:
                yield AgentEvent("answer", message.content)
            return
        if "__interrupt__" in chunk:
            item = chunk["__interrupt__"][0]  # one question at a time; the others come back after this one is answered
            yield AgentEvent("needs_input", payload={**dict(item.value), "interrupt_id": item.id})
            return
        for node, update in chunk.items():
            if not isinstance(update, dict):
                continue
            if node.endswith("before_model") and "steps" in update:
                yield AgentEvent("step", step=update["steps"])
            elif node == "model":
                for tool_call in (update["messages"][-1].tool_calls or []):
                    yield AgentEvent("tool_start", name=tool_call["name"], args=dict(tool_call["args"]))
            elif node == "tools":
                for message in update.get("messages", []):
                    if not isinstance(message, ToolMessage):
                        continue
                    artifact = message.artifact if isinstance(message.artifact, dict) else {}
                    ok = artifact.get("ok", getattr(message, "status", "success") != "error")
                    yield AgentEvent(
                        "tool_result",
                        _plain(message.content),
                        name=message.name or "",
                        ok=ok,
                        urls=tuple(artifact.get("fetched", ())),
                    )
                    if artifact.get("rows_added"):
                        yield AgentEvent("rows_added", count=artifact["rows_added"])

    async def _terminal(self, config: dict) -> AgentEvent:
        state = await self.agent.aget_state(config)
        reason = state.values.get("end_reason")
        if reason:
            return AgentEvent("limit", reason)
        messages = state.values.get("messages") or []
        final = messages[-1] if messages else None
        if isinstance(final, AIMessage) and not final.tool_calls and _plain(final.content).strip():
            return AgentEvent("done")
        final_flag = {"final": True}  # resuming cannot change this: the graph already ended
        if isinstance(final, AIMessage) and final.invalid_tool_calls and not final.tool_calls:
            return AgentEvent(
                "error",
                "The model sent a tool call that was not valid JSON, so the run ended. Try again or rephrase the instruction.",
                payload=final_flag,
            )
        return AgentEvent("error", "The model returned an empty answer.", payload=final_flag)
