"""OpenAI-compatible model clients (LM Studio, Mistral) with reasoning-aware streaming."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import httpx
import openai

CHAT_KINDS = {"llm", "vlm"}


class LLMUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    kind: str = "llm"
    loaded: bool = False
    context: int | None = None


@dataclass(frozen=True)
class Event:
    kind: Literal["reasoning", "answer", "error"]
    text: str


def split_content(content) -> tuple[str, str]:
    """Split a delta's content into (reasoning, answer). Mistral reasoning models send a list of
    typed chunks while thinking; everything else sends a plain string."""
    if content is None:
        return "", ""
    if isinstance(content, str):
        return "", content
    reasoning, answer = [], []
    for part in content:
        if isinstance(part, str):
            answer.append(part)
        elif part.get("type") == "thinking":
            thought = part.get("thinking")
            reasoning.append(thought if isinstance(thought, str) else "".join(t.get("text", "") for t in thought or []))
        elif part.get("type") == "text":
            answer.append(part.get("text", ""))
    return "".join(reasoning), "".join(answer)


def _partial_suffix_len(text: str, tag: str) -> int:
    for size in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:size]):
            return size
    return 0


class ThinkSplitter:
    """Splits inline <think>...</think> blocks out of a token stream."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buf += text
        out: list[tuple[str, str]] = []
        while True:
            tag = self.CLOSE if self._in_think else self.OPEN
            kind = "reasoning" if self._in_think else "answer"
            index = self._buf.find(tag)
            if index >= 0:
                if index:
                    out.append((kind, self._buf[:index]))
                self._buf = self._buf[index + len(tag) :]
                self._in_think = not self._in_think
                continue
            keep = _partial_suffix_len(self._buf, tag)
            emit = self._buf[: len(self._buf) - keep]
            if emit:
                out.append((kind, emit))
            self._buf = self._buf[len(self._buf) - keep :]
            return out

    def flush(self) -> list[tuple[str, str]]:
        if not self._buf:
            return []
        kind = "reasoning" if self._in_think else "answer"
        text, self._buf = self._buf, ""
        return [(kind, text)]


class OpenAICompatible:
    """Shared streaming, context-window and error handling. Subclasses say how to list models."""

    key = "model-server"  # matches the SCRAPER_PROVIDER value
    name = "model server"
    hosted = False  # True when page content leaves this machine

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        fallback_context: int = 16384,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fallback_context = fallback_context
        headers = {"Authorization": f"Bearer {api_key}"} if self.hosted else {}
        self._http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0), headers=headers)
        self._client = openai.AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(600.0, connect=5.0)),
        )
        self._models: dict[str, ModelInfo] = {}

    async def list_models(self) -> list[ModelInfo]:
        raise NotImplementedError

    async def context_length(self, model: str) -> int:
        info = self._models.get(model)
        if info is None or not info.loaded:
            try:
                await self.list_models()
            except LLMUnavailable:
                pass
            info = self._models.get(model)
        return info.context if info and info.context else self.fallback_context

    async def stream_chat(self, model: str, messages: list[dict]) -> AsyncIterator[Event]:
        splitter = ThinkSplitter()
        try:
            stream = await self._client.chat.completions.create(model=model, messages=messages, stream=True)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield Event("reasoning", reasoning)
                thought, answer = split_content(delta.content)
                if thought:
                    yield Event("reasoning", thought)
                if answer:
                    for kind, text in splitter.feed(answer):
                        yield Event(kind, text)
            for kind, text in splitter.flush():
                yield Event(kind, text)
        except (openai.OpenAIError, httpx.HTTPError) as exc:
            yield Event("error", self._describe(exc))

    def _describe(self, exc: Exception) -> str:
        if isinstance(exc, openai.APIStatusError):
            if exc.status_code in (401, 403):
                return f"{self.name} rejected the API key. Check SCRAPER_API_KEY."
            return f"{self.name} rejected the request ({exc.status_code}): {exc.message}"
        if isinstance(exc, (openai.APIConnectionError, httpx.HTTPError)):
            return f"Cannot reach {self.name} at {self.base_url}"
        return str(exc)


class LMStudio(OpenAICompatible):
    key = "lmstudio"
    name = "LM Studio"

    def __init__(
        self,
        base_url: str,
        *,
        fallback_context: int = 16384,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, "lm-studio", fallback_context=fallback_context, transport=transport)
        self._origin = self.base_url.removesuffix("/v1")

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._http.get(f"{self._origin}/api/v0/models")
            resp.raise_for_status()
            models = [
                ModelInfo(
                    id=m["id"],
                    kind=m.get("type", "llm"),
                    loaded=m.get("state") == "loaded",
                    context=m.get("loaded_context_length"),
                )
                for m in resp.json()["data"]
                if m.get("type", "llm") in CHAT_KINDS
            ]
        except (httpx.HTTPError, KeyError, ValueError):
            try:
                resp = await self._http.get(f"{self.base_url}/models")
                resp.raise_for_status()
                models = [ModelInfo(id=m["id"]) for m in resp.json()["data"]]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise LLMUnavailable(f"Cannot reach LM Studio at {self.base_url}") from exc
        models.sort(key=lambda m: (not m.loaded, m.id))
        self._models = {m.id: m for m in models}
        return models


class MistralAPI(OpenAICompatible):
    """The hosted Mistral API. Page content is sent to Mistral's servers."""

    key = "mistral"
    name = "Mistral"
    hosted = True

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._http.get(f"{self.base_url}/models")
            resp.raise_for_status()
            models = [
                ModelInfo(id=m["id"], kind="llm", loaded=True, context=m.get("max_context_length"))
                for m in resp.json()["data"]
                if m.get("capabilities", {}).get("completion_chat") and m["capabilities"].get("function_calling")
            ]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise LLMUnavailable("Mistral rejected the API key. Check SCRAPER_API_KEY.") from exc
            raise LLMUnavailable(f"Mistral returned {exc.response.status_code} listing models") from exc
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMUnavailable(f"Cannot reach Mistral at {self.base_url}") from exc
        models.sort(key=lambda m: m.id)
        self._models = {m.id: m for m in models}
        return models
