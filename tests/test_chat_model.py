import inspect
import json

import httpx
from langchain_openai import ChatOpenAI

from scraper.chat_model import ReasoningChat, make_chat_model
from scraper.config import Config


def chunk(**delta):
    return {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


def make_model(*payloads):
    def handler(request):
        body = "".join(f"data: {json.dumps(p)}\n\n" for p in payloads) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    return ReasoningChat(
        model="m",
        base_url="http://lm.test/v1",
        api_key="x",
        streaming=True,
        max_retries=0,
        http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def collect(model):
    reasoning, text, tool_chunks = "", "", []
    async for piece in model.astream("hello"):
        reasoning += piece.additional_kwargs.get("reasoning_content", "")
        text += piece.content if isinstance(piece.content, str) else ""
        tool_chunks += piece.tool_call_chunks
    return reasoning, text, tool_chunks


async def test_reasoning_content_is_kept_separate_from_the_answer():
    model = make_model(chunk(reasoning_content="think "), chunk(reasoning_content="hard"), chunk(content="Hi"), chunk(content=" there"))
    reasoning, text, _ = await collect(model)
    assert reasoning == "think hard"
    assert text == "Hi there"


async def test_tool_call_fragments_still_stream_through():
    call = {"index": 0, "id": "c1", "type": "function", "function": {"name": "fetch_page", "arguments": '{"url":"a"}'}}
    _, _, tool_chunks = await collect(make_model(chunk(tool_calls=[call])))
    assert tool_chunks and tool_chunks[0]["name"] == "fetch_page"


def test_the_private_hook_the_shim_overrides_has_not_changed():
    # If a LangChain upgrade changes this signature the reasoning display silently dies. Fail loudly instead.
    params = list(inspect.signature(ChatOpenAI._convert_chunk_to_generation_chunk).parameters)
    assert params == ["self", "chunk", "default_chunk_class", "base_generation_info"]


def test_make_chat_model_points_at_lm_studio():
    model = make_chat_model(Config(base_url="http://lm.test:1234/v1"), "qwen-test")
    assert model.model_name == "qwen-test"
    assert model.openai_api_base == "http://lm.test:1234/v1"
    assert model.streaming is True


async def test_magistral_thinking_chunks_become_reasoning_and_the_answer_stays_text():
    thinking = [{"type": "thinking", "thinking": [{"type": "text", "text": "plan "}]}]
    transition = [{"type": "thinking", "thinking": [{"type": "text", "text": "done"}]}, {"type": "text", "text": "Hel"}]
    reasoning, text, _ = await collect(make_model(chunk(content=thinking), chunk(content=transition), chunk(content="lo")))
    assert reasoning == "plan done"
    assert text == "Hello"


def test_make_chat_model_for_mistral_uses_its_own_endpoint_and_key():
    cfg = Config(api_key="sk-test", base_url="http://lm.test:1234/v1", mistral_base_url="https://mistral.test/v1")
    model = make_chat_model(cfg, "mistral-small-latest", "mistral")
    assert isinstance(model, ReasoningChat)
    assert model.openai_api_base == "https://mistral.test/v1"
    assert model.openai_api_key.get_secret_value() == "sk-test"


def test_the_provider_argument_wins_over_the_configured_start_provider():
    cfg = Config(provider="mistral", api_key="sk-test")
    assert make_chat_model(cfg, "qwen-test", "lmstudio").openai_api_key.get_secret_value() == "lm-studio"


def test_make_chat_model_for_lm_studio_still_uses_the_placeholder_key():
    model = make_chat_model(Config(), "qwen-test")
    assert model.openai_api_key.get_secret_value() == "lm-studio"
