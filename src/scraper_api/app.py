"""The FastAPI application: wiring, middleware and start-up."""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from scraper.config import Config
from scraper.llm import MistralAPI
from scraper.usage import UsageStore

from .auth import JWKSVerifier
from .catalog import ModelCatalog
from .db import make_pool, migrate, open_checkpointer
from .deps import Deps
from .errors import ApiError, api_error_handler, validation_error_handler
from .pgstore import PgUsageStore
from .registry import RateLimiter, RunSlots, SessionRegistry
from .routes import router as state_router
from .streams import router as stream_router

log = logging.getLogger("scraper_api")
EVICT_EVERY_SECONDS = 60


async def build_deps(cfg: Config, stack: AsyncExitStack) -> Deps:
    pool = make_pool(cfg.database_url)
    stack.callback(pool.close)
    migrate(pool)
    checkpointer = await open_checkpointer(cfg.database_url, stack)
    llm = MistralAPI(cfg.mistral_base_url, cfg.api_key, fallback_context=cfg.fallback_context)
    usage: UsageStore = PgUsageStore(pool)
    return Deps(
        cfg=cfg,
        verifier=JWKSVerifier(cfg.jwks_url, cfg.jwt_issuer, cfg.jwt_audience),
        registry=SessionRegistry(cfg, pool=pool, checkpointer=checkpointer, llm=llm, usage_store=usage),
        slots=RunSlots(cfg.max_concurrent_runs),
        limiter=RateLimiter(cfg.requests_per_minute),
        catalog=ModelCatalog(llm, cfg.allowed_model_list),
        pool=pool,
        checkpointer=checkpointer,
    )


def create_app(cfg: Config, deps: Deps | None = None) -> FastAPI:
    """`deps` is given by tests; in production it is built at start-up from the configuration."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stack = AsyncExitStack()
        if deps is None:
            app.state.deps = await build_deps(cfg, stack)

        async def sweep() -> None:
            while True:
                await asyncio.sleep(EVICT_EVERY_SECONDS)
                app.state.deps.registry.evict_idle()

        task = asyncio.create_task(sweep())
        try:
            yield
        finally:
            task.cancel()
            await stack.aclose()

    app = FastAPI(title="Page Chat API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if deps is not None:
        app.state.deps = deps
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origin_list),
        allow_origin_regex=cfg.cors_origin_regex or None,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,  # tokens travel in a header, never in cookies
        max_age=600,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    app.include_router(state_router)
    app.include_router(stream_router)
    return app
