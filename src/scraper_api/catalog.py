"""The models users may pick: what the provider lists, narrowed by SCRAPER_ALLOWED_MODELS, cached for a few minutes."""

import time
from collections.abc import Callable

from scraper.llm import LLMUnavailable, ModelInfo

from .errors import ApiError


class ModelCatalog:
    def __init__(self, llm, allowed: tuple[str, ...] = (), *, ttl: float = 300.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.llm = llm
        self.allowed = allowed
        self.ttl = ttl
        self._clock = clock
        self._cached: list[ModelInfo] | None = None
        self._fetched_at = 0.0

    async def models(self) -> list[ModelInfo]:
        """Raises LLMUnavailable only when the provider cannot be reached and nothing is cached."""
        fresh = self._cached is not None and self._clock() - self._fetched_at < self.ttl
        if not fresh:
            try:
                listed = await self.llm.list_models()
            except LLMUnavailable:
                if self._cached is None:
                    raise
            else:
                self._cached = [m for m in listed if not self.allowed or m.id in self.allowed]
                self._fetched_at = self._clock()
        return list(self._cached)

    async def safe_models(self) -> tuple[list[ModelInfo], str | None]:
        try:
            return await self.models(), None
        except LLMUnavailable as exc:
            return [], str(exc)

    async def require(self, model_id: str) -> ModelInfo:
        try:
            models = await self.models()
        except LLMUnavailable as exc:
            raise ApiError(503, "model_unavailable", str(exc)) from exc
        for model in models:
            if model.id == model_id:
                return model
        raise ApiError(400, "unknown_model", "That model is not available. Pick one from the list.")
