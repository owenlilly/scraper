"""LM Studio chat model for LangChain: ChatOpenAI that keeps the separate reasoning stream."""

from langchain_openai import ChatOpenAI

from .config import Config


class LMStudioChat(ChatOpenAI):
    """LM Studio streams Qwen's reasoning in `delta.reasoning_content`, which ChatOpenAI drops."""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if generation is not None and choices and choices[0].get("delta"):
            reasoning = choices[0]["delta"].get("reasoning_content")
            if reasoning:
                generation.message.additional_kwargs["reasoning_content"] = reasoning
        return generation


def make_chat_model(cfg: Config, model_id: str, **overrides) -> LMStudioChat:
    return LMStudioChat(
        model=model_id,
        base_url=cfg.base_url,
        api_key="lm-studio",
        streaming=True,
        max_retries=0,
        timeout=600,
        **overrides,
    )
