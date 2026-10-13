import json

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from fakes import FakeLLM, ScriptedChat, make_fetch, make_page
from jwks_support import AUDIENCE, ISSUER, JWKSServer, Signer
from scraper.config import Config
from scraper.llm import Event, ModelInfo
from scraper.usage import MemoryUsageStore
from scraper.api.app import create_app
from scraper.api.auth import JWKSVerifier
from scraper.api.catalog import ModelCatalog
from scraper.api.deps import Deps
from scraper.api.registry import RateLimiter, RunSlots, SessionRegistry

A = "https://site.test/a"
B = "https://site.test/b"
WEB = {A: make_page(A, title="Page A", markdown="# A\n\nStarter is $10."), B: make_page(B, title="Page B")}
MODEL = "qwen-test"


class Harness:
    """The whole API on a real Postgres, with a scripted model, a fake web and a throwaway identity provider."""

    def __init__(self, pg, url_check=None, pipeline=None, **cfg_overrides):
        self.signer = Signer("k1")
        self.idp = JWKSServer(self.signer)
        self.cfg = Config(
            hosted=True, request_delay=0.0, cors_origins="https://web.test", service_secret="s3cret", max_stored_pages=20,
            **cfg_overrides,
        )
        self.llm = FakeLLM(events=[Event("reasoning", "hmm"), Event("answer", "Ten dollars.")],
                           models=[ModelInfo(MODEL, "llm", True, 100_000), ModelInfo("other-model", "llm", True, 100_000)])
        self.model = ScriptedChat()
        self.pool = pg
        self.pipeline = pipeline
        self.url_check = url_check or (lambda url: None)
        self.web = dict(WEB)
        self.before_fetch = None  # a test may set this to run (or block) inside the fake fetch

    def _fetch(self, url, mode="fast", *, timeout=20.0):
        if self.before_fetch is not None:
            self.before_fetch(url)
        return make_fetch(self.web)(url, mode, timeout=timeout)

    def build(self):
        registry = SessionRegistry(
            self.cfg, pool=self.pool, checkpointer=InMemorySaver(), llm=self.llm, llms=getattr(self, "llms", None), usage_store=MemoryUsageStore(),
            fetch_base=self._fetch,
            url_check=self.url_check, model_factory=lambda model_id: self.model,
        )
        self.deps = Deps(
            cfg=self.cfg, verifier=JWKSVerifier(self.idp.url, ISSUER, AUDIENCE), registry=registry,
            slots=RunSlots(self.cfg.max_concurrent_runs), limiter=RateLimiter(self.cfg.requests_per_minute),
            catalogs={k: ModelCatalog(v, self.cfg.allowed_model_list) for k, v in (getattr(self, "llms", None) or {self.llm.key: self.llm}).items()},
            pool=self.pool, pipeline=self.pipeline,
        )
        self.app = create_app(self.cfg, self.deps)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://api.test")
        return self

    def auth(self, user="alice", **claims):
        return {"Authorization": f"Bearer {self.signer.token(user, **claims)}"}

    async def close(self):
        await self.client.aclose()
        self.idp.close()


def frames(response):
    """Parse a text/event-stream body into [(event name, data)]."""
    out = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        out.append((name, data))
    return out


def names(response):
    return [n for n, _ in frames(response)]


async def seed_page(api, user="alice", url=A):
    """Give the user a scraped page by going straight to their session, so a test needs no route to do it."""
    entry = await api.deps.registry.get(user)
    await entry.session.scrape(url)
    return entry
