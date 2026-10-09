import json

import httpx
import pytest

from scraper.llm import Event, LLMUnavailable, LMStudio, MistralAPI, ModelInfo, ThinkSplitter, split_content

BASE = "http://lm.test/v1"
NATIVE = {
    "data": [
        {"id": "embed-x", "type": "embeddings", "state": "not-loaded", "max_context_length": 2048, "loaded_context_length": None},
        {"id": "cold", "type": "llm", "state": "not-loaded", "max_context_length": 32768},
        {"id": "big-q", "type": "vlm", "state": "loaded", "max_context_length": 262144, "loaded_context_length": 208384},
    ]
}


def chunk(**delta):
    return {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


def sse(*payloads):
    body = "".join(f"data: {json.dumps(p)}\n\n" for p in payloads) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def client(handler) -> LMStudio:
    return LMStudio(BASE, fallback_context=16384, transport=httpx.MockTransport(handler))


async def collect(stream):
    return [event async for event in stream]


def joined(events, kind):
    return "".join(e.text for e in events if e.kind == kind)


# ---- ThinkSplitter ----------------------------------------------------------


def test_think_splitter_handles_tags_split_across_chunks():
    splitter = ThinkSplitter()
    out = []
    for piece in ["<thi", "nk>pla", "n</th", "ink>Ans", "wer"]:
        out += splitter.feed(piece)
    out += splitter.flush()
    assert "".join(t for k, t in out if k == "reasoning") == "plan"
    assert "".join(t for k, t in out if k == "answer") == "Answer"


def test_think_splitter_does_not_swallow_a_lone_angle_bracket():
    splitter = ThinkSplitter()
    out = splitter.feed("1 <") + splitter.feed(" 2") + splitter.flush()
    assert "".join(t for k, t in out if k == "answer") == "1 < 2"


def test_think_splitter_flushes_a_trailing_partial_tag():
    splitter = ThinkSplitter()
    out = splitter.feed("done <th") + splitter.flush()
    assert "".join(t for _, t in out) == "done <th"


# ---- list_models / context_length ------------------------------------------


async def test_list_models_filters_embeddings_and_puts_loaded_first():
    llm = client(lambda req: httpx.Response(200, json=NATIVE))
    models = await llm.list_models()
    assert [m.id for m in models] == ["big-q", "cold"]
    assert models[0] == ModelInfo("big-q", "vlm", True, 208384)
    assert models[1].loaded is False


async def test_list_models_falls_back_to_openai_endpoint():
    def handler(req):
        if req.url.path == "/api/v0/models":
            return httpx.Response(404)
        return httpx.Response(200, json={"data": [{"id": "only-model"}]})

    assert [m.id for m in await client(handler).list_models()] == ["only-model"]


async def test_list_models_raises_when_server_is_down():
    def handler(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMUnavailable):
        await client(handler).list_models()


async def test_context_length_uses_loaded_value_else_fallback():
    # Review Focus 5: model not loaded -> no loaded context length
    llm = client(lambda req: httpx.Response(200, json=NATIVE))
    assert await llm.context_length("big-q") == 208384
    assert await llm.context_length("cold") == 16384
    assert await llm.context_length("never-heard-of-it") == 16384


async def test_context_length_falls_back_when_server_is_down():
    def handler(req):
        raise httpx.ConnectError("refused")

    assert await client(handler).context_length("x") == 16384


# ---- stream_chat ------------------------------------------------------------


async def test_stream_separates_reasoning_content_from_answer():
    llm = client(lambda req: sse(chunk(reasoning_content="think "), chunk(reasoning_content="hard"), chunk(content="Hi"), chunk(content=" there")))
    events = await collect(llm.stream_chat("m", [{"role": "user", "content": "hello"}]))
    assert joined(events, "reasoning") == "think hard"
    assert joined(events, "answer") == "Hi there"
    assert not [e for e in events if e.kind == "error"]


async def test_stream_splits_inline_think_tags():
    llm = client(lambda req: sse(chunk(content="<think>plan</thi"), chunk(content="nk>Answer")))
    events = await collect(llm.stream_chat("m", []))
    assert joined(events, "reasoning") == "plan"
    assert joined(events, "answer") == "Answer"


async def test_stream_sends_model_messages_and_stream_flag():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return sse(chunk(content="ok"))

    await collect(client(handler).stream_chat("qwen", [{"role": "user", "content": "q"}]))
    assert seen["model"] == "qwen"
    assert seen["stream"] is True
    assert seen["messages"] == [{"role": "user", "content": "q"}]


async def test_stream_reports_unreachable_server_as_an_error_event():
    def handler(req):
        raise httpx.ConnectError("refused")

    events = await collect(client(handler).stream_chat("m", []))
    assert [e.kind for e in events] == ["error"]
    assert "Cannot reach LM Studio" in events[0].text


async def test_stream_reports_http_errors_with_status_and_message():
    body = {"error": {"message": "n_ctx exceeded", "type": "invalid_request_error"}}
    events = await collect(client(lambda req: httpx.Response(400, json=body)).stream_chat("m", []))
    assert events[0].kind == "error"
    assert "400" in events[0].text and "n_ctx exceeded" in events[0].text


async def test_stream_with_no_content_yields_no_events():
    # Review Focus 5: the Session decides what an empty answer means
    events = await collect(client(lambda req: sse()).stream_chat("m", []))
    assert events == []


# ---- MistralAPI -------------------------------------------------------------

MISTRAL_BASE = "https://mistral.test/v1"
MISTRAL_MODELS = {
    "data": [
        {"id": "mistral-embed", "capabilities": {"completion_chat": False, "function_calling": False}, "max_context_length": 8192},
        {"id": "codestral-2508", "capabilities": {"completion_chat": True, "function_calling": False}, "max_context_length": 256000},
        {"id": "mistral-small-latest", "capabilities": {"completion_chat": True, "function_calling": True}, "max_context_length": 131072},
        {"id": "magistral-medium-latest", "capabilities": {"completion_chat": True, "function_calling": True}, "max_context_length": 40960},
    ]
}


def mistral(handler) -> MistralAPI:
    return MistralAPI(MISTRAL_BASE, "sk-test", fallback_context=16384, transport=httpx.MockTransport(handler))


def test_providers_have_stable_keys_matching_the_config_names():
    assert LMStudio(BASE).key == "lmstudio"
    assert MistralAPI(MISTRAL_BASE, "k").key == "mistral"


def test_providers_know_their_display_names():
    assert LMStudio(BASE).name == "LM Studio"
    assert MistralAPI(MISTRAL_BASE, "k").name == "Mistral"


async def test_mistral_lists_only_tool_capable_chat_models_all_loaded():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=MISTRAL_MODELS)

    models = await mistral(handler).list_models()
    assert seen == {"url": f"{MISTRAL_BASE}/models", "auth": "Bearer sk-test"}
    assert [m.id for m in models] == ["magistral-medium-latest", "mistral-small-latest"]
    assert models[1] == ModelInfo("mistral-small-latest", "llm", True, 131072)
    assert all(m.loaded for m in models)


async def test_mistral_context_length_comes_from_the_model_list():
    llm = mistral(lambda req: httpx.Response(200, json=MISTRAL_MODELS))
    assert await llm.context_length("mistral-small-latest") == 131072
    assert await llm.context_length("unknown-model") == 16384


async def test_mistral_bad_key_on_listing_says_so():
    with pytest.raises(LLMUnavailable, match="API key"):
        await mistral(lambda req: httpx.Response(401, json={"message": "Unauthorized"})).list_models()


async def test_mistral_unreachable_on_listing_names_mistral():
    def handler(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMUnavailable, match="Cannot reach Mistral"):
        await mistral(handler).list_models()


async def test_mistral_stream_sends_the_bearer_key():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return sse(chunk(content="ok"))

    await collect(mistral(handler).stream_chat("mistral-small-latest", []))
    assert seen["auth"] == "Bearer sk-test"


async def test_mistral_stream_rejected_key_is_a_readable_error():
    events = await collect(mistral(lambda req: httpx.Response(401, json={"message": "Unauthorized"})).stream_chat("m", []))
    assert [e.kind for e in events] == ["error"]
    assert "Mistral rejected the API key" in events[0].text


async def test_mistral_stream_unreachable_names_mistral_not_lm_studio():
    def handler(req):
        raise httpx.ConnectError("refused")

    events = await collect(mistral(handler).stream_chat("m", []))
    assert "Cannot reach Mistral" in events[0].text
    assert "LM Studio" not in events[0].text


async def test_stream_reads_magistral_thinking_chunks_as_reasoning():
    thinking = [{"type": "thinking", "thinking": [{"type": "text", "text": "plan "}]}]
    transition = [{"type": "thinking", "thinking": [{"type": "text", "text": "done"}]}, {"type": "text", "text": "Hel"}]
    events = await collect(mistral(lambda req: sse(chunk(content=thinking), chunk(content=transition), chunk(content="lo"))).stream_chat("m", []))
    assert joined(events, "reasoning") == "plan done"
    assert joined(events, "answer") == "Hello"


# ---- split_content ----------------------------------------------------------


def test_split_content_passes_plain_strings_through():
    assert split_content("hi") == ("", "hi")
    assert split_content(None) == ("", "")


def test_split_content_separates_thinking_from_text_chunks():
    parts = [{"type": "thinking", "thinking": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}, {"type": "text", "text": "c"}]
    assert split_content(parts) == ("ab", "c")
