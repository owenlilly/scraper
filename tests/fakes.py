import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field
from scraper.fetch import FetchError, Page


def make_page(url, *, title=None, markdown=None, links=(), looks_empty=False, html="") -> Page:
    label = title or url
    body = markdown if markdown is not None else f"# {label}\n\nBody text for {url}."
    return Page(url=url, title=label, markdown=body, links=tuple(links), looks_empty=looks_empty, html=html)


def make_fetch(web: dict[str, Page], calls: list | None = None):
    def fetch_fn(url, mode="fast", *, timeout=20.0):
        if calls is not None:
            calls.append((url, mode))
        if url not in web:
            raise FetchError(f"HTTP 404 from {url}")
        return web[url]

    return fetch_fn


class FakeLLM:
    """Stands in for LMStudio in session and UI tests."""

    def __init__(self, events=(), window=100_000, models=None):
        from scraper.llm import ModelInfo  # imported lazily: scraper.llm arrives in Task 5

        self.events: list = list(events)
        self.window = window
        self.models = models or [ModelInfo("qwen-test", "llm", True, window)]
        self.calls: list[list[dict]] = []

    async def list_models(self):
        return self.models

    async def context_length(self, model):
        return self.window

    async def stream_chat(self, model, messages):
        self.calls.append(messages)
        for event in self.events:
            yield event


def call(name, id="c1", **args):
    return {"name": name, "args": args, "id": id, "type": "tool_call"}


def ai(content="", *calls, reasoning=None):
    extra = {"reasoning_content": reasoning} if reasoning else {}
    return AIMessage(content=content, tool_calls=list(calls), additional_kwargs=extra)


class ScriptedChat(BaseChatModel):
    """Replays scripted AIMessages and records what it was asked, for driving real agent graphs."""

    script: list = Field(default_factory=list)
    calls: list = Field(default_factory=list)
    bound: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"bound": [getattr(t, "name", str(t)) for t in tools]})

    def _next(self, messages):
        self.calls.append({"messages": list(messages), "tools": list(self.bound)})
        item = self.script.pop(0) if self.script else AIMessage(content="(script exhausted)")
        if isinstance(item, BaseException):
            raise item
        return item

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._next(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(content=""))
        for word in message.content.split(" ") if message.content else []:
            yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))
        reasoning = message.additional_kwargs.get("reasoning_content")
        if reasoning:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", additional_kwargs={"reasoning_content": reasoning}))
        if message.tool_calls:
            chunks = [
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(message.tool_calls)
            ]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=chunks))
        if message.invalid_tool_calls:
            chunks = [
                {"name": c["name"], "args": c["args"], "id": c["id"], "index": i}
                for i, c in enumerate(message.invalid_tool_calls)
            ]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=chunks))
