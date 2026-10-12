"""Verify the bearer tokens the web app's Better Auth issues, using the public keys it publishes."""

import threading
import time
from collections.abc import Callable

import httpx
import jwt
from jwt import PyJWK, PyJWKSet

MAX_TOKEN_CHARS = 4096
LEEWAY_SECONDS = 10


class AuthError(Exception):
    """The token is missing, malformed, expired or not meant for this API."""


class AuthUnavailable(Exception):
    """The token could not be checked because the key server cannot be reached."""


class JWKSVerifier:
    """Keeps its own copy of the key server's keys, so that:

    - a key seen before keeps working while the key server is down (stale-if-error), but
    - the keys are fetched again every `ttl` seconds, so a withdrawn key stops being trusted, and
    - an unknown key id triggers at most one forced refresh per `refresh_interval`, so a stranger's forged ids cannot
      make this API hammer the web app, and a freshly rotated-in key is still picked up at once.

    (PyJWT's own PyJWKClient was not used: with key caching on it remembers a key id for the life of the process, which
    would trust a withdrawn key forever.)"""

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        *,
        algorithms: tuple[str, ...] = ("EdDSA",),
        ttl: float = 300.0,
        refresh_interval: float = 30.0,
        retry_after: float = 15.0,
        timeout: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.audience = audience
        self.algorithms = list(algorithms)
        self.ttl = ttl
        self.refresh_interval = refresh_interval
        self.retry_after = retry_after
        self._clock = clock
        self._http = httpx.Client(timeout=timeout, follow_redirects=False)
        self._lock = threading.Lock()  # one fetch at a time: everyone else waits and then finds the keys already refreshed
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        self._next_try: float | None = None
        self._last_forced: float | None = None

    def _fetch(self) -> None:
        """Replace the key set from the key server. Raises AuthUnavailable, leaving the old keys in place, if it cannot."""
        try:
            response = self._http.get(self.jwks_url)
            response.raise_for_status()
            keys = {key.key_id: key for key in PyJWKSet.from_dict(response.json()).keys if key.key_id}
            if not keys:
                raise ValueError("the key set holds no usable keys")
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            raise AuthUnavailable("Cannot reach the sign-in service's key server") from exc
        self._keys, self._fetched_at, self._next_try = keys, self._clock(), None

    def _key_for(self, kid: str) -> PyJWK:
        with self._lock:
            now = self._clock()
            due = self._fetched_at is None or now - self._fetched_at >= self.ttl
            if due and (self._next_try is None or now >= self._next_try):
                try:
                    self._fetch()
                except AuthUnavailable:
                    self._next_try = now + self.retry_after  # remember the failure, so a dead server is not asked on every request
                    if not self._keys:
                        raise
            if not self._keys:
                raise AuthUnavailable("Cannot reach the sign-in service's key server")
            key = self._keys.get(kid)
            if key is None and (self._last_forced is None or now - self._last_forced >= self.refresh_interval):
                self._last_forced = now  # an unknown key id: it may have been rotated in
                self._fetch()
                key = self._keys.get(kid)
            if key is None:
                raise AuthError("Unknown signing key")
            return key

    def verify(self, token: str) -> dict:
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise AuthError("Missing or oversized token")
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            if not isinstance(kid, str) or not kid:
                raise AuthError("Token has no key id")
            key = self._key_for(kid)
            return jwt.decode(
                token,
                key.key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except (AuthError, AuthUnavailable):
            raise
        except jwt.PyJWTError as exc:
            raise AuthError(str(exc)) from exc

    def user_id(self, token: str) -> str:
        sub = self.verify(token).get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise AuthError("Token has no subject")
        return sub
