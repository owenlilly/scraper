"""What the routes share: the service objects, and the dependency that turns a bearer token into a user."""

from dataclasses import dataclass

from fastapi import Depends, Request
from psycopg_pool import ConnectionPool
from sqlalchemy import Engine

from scraper.config import Config
from scraper.pipeline.blobs import FilesystemBlobStore
from scraper.pipeline.spider import SourceSpider

from .auth import AuthError, AuthUnavailable, JWKSVerifier
from .catalog import ModelCatalog
from .errors import ApiError
from .registry import Entry, RateLimited, RateLimiter, RunSlots, SessionRegistry


@dataclass
class PipelineAccess:
    """What the spiders page's routes need: the pipeline's database, its blob store, and the spiders that can run."""

    engine: Engine
    blobs: FilesystemBlobStore
    spiders: dict[str, type[SourceSpider]]


@dataclass
class Deps:
    cfg: Config
    verifier: JWKSVerifier
    registry: SessionRegistry
    slots: RunSlots
    limiter: RateLimiter
    catalogs: dict[str, ModelCatalog]  # one per provider people may choose
    pool: ConnectionPool
    pipeline: PipelineAccess | None = None

    def catalog_for(self, provider: str) -> ModelCatalog:
        return self.catalogs[provider]


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


def current_user(request: Request, deps: Deps = Depends(get_deps)) -> str:
    """The signed-in user's id. A plain `def`, so FastAPI runs it in a thread: fetching keys can block."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "unauthorized", "Sign in to continue.", headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = deps.verifier.user_id(token.strip())
    except AuthError:
        raise ApiError(401, "unauthorized", "Your sign-in has expired. Sign in again.", headers={"WWW-Authenticate": "Bearer"}) from None
    except AuthUnavailable:
        raise ApiError(503, "auth_unavailable", "Sign-in checks are unavailable right now. Try again shortly.") from None
    try:
        deps.limiter.check(user_id)
    except RateLimited as exc:
        raise ApiError(429, "rate_limited", str(exc), headers={"Retry-After": str(exc.retry_after)}) from None
    return user_id


async def get_entry(user_id: str = Depends(current_user), deps: Deps = Depends(get_deps)) -> Entry:
    return await deps.registry.get(user_id)
