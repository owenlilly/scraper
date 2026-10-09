"""Chat model for LangChain: ChatOpenAI that keeps the separate reasoning stream."""

import warnings

from langchain_openai import ChatOpenAI

from .config import Config
from .llm import split_content

# The OpenAI SDK types `delta.content` as str, so dumping Mistral's list-style thinking chunks warns.
# The data is intact and ReasoningChat splits it, so silence just that message.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings", category=UserWarning, module="pydantic.main")


class ReasoningChat(ChatOpenAI):
    """Keeps reasoning that ChatOpenAI drops: LM Studio's `delta.reasoning_content`, and the typed
    thinking chunks Mistral reasoning models put in `delta.content`."""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if generation is not None and choices and choices[0].get("delta"):
            delta = choices[0]["delta"]
            reasoning = delta.get("reasoning_content")
            if isinstance(delta.get("content"), list):
                thought, generation.message.content = split_content(delta["content"])
                reasoning = (reasoning or "") + thought
            if reasoning:
                generation.message.additional_kwargs["reasoning_content"] = reasoning
        return generation


def make_chat_model(cfg: Config, model_id: str, provider: str = "lmstudio", **overrides) -> ReasoningChat:
    hosted = provider == "mistral"
    return ReasoningChat(
        model=model_id,
        base_url=cfg.mistral_base_url if hosted else cfg.base_url,
        api_key=cfg.api_key if hosted else "lm-studio",
        streaming=True,
        max_retries=0,
        timeout=600,
        **overrides,
    )
