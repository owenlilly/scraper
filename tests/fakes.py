from scraper.fetch import FetchError, Page


def make_page(url, *, title=None, markdown=None, links=(), looks_empty=False) -> Page:
    label = title or url
    body = markdown if markdown is not None else f"# {label}\n\nBody text for {url}."
    return Page(url=url, title=label, markdown=body, links=tuple(links), looks_empty=looks_empty)


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
