"""Verify the bearer tokens the web app's Better Auth issues, using the public keys it publishes."""

import threading
import time
from collections.abc import Callable

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

MAX_TOKEN_CHARS = 4096
LEEWAY_SECONDS = 10


class AuthError(Exception):
    """The token is missing, malformed, expired or not meant for this API."""


class AuthUnavailable(Exception):
    """The token could not be checked because the key server cannot be reached."""


class JWKSVerifier:
    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        *,
        algorithms: tuple[str, ...] = ("EdDSA",),
        refresh_interval: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.algorithms = list(algorithms)
        self.refresh_interval = refresh_interval
        self._clock = clock
        self._client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)
        self._lock = threading.Lock()
        self._last_refresh: float | None = None

    def _signing_key(self, token: str):
        try:
            return self._client.get_signing_key_from_jwt(token)
        except PyJWKClientConnectionError as exc:
            raise AuthUnavailable("Cannot reach the sign-in service's key server") from exc
        except PyJWKClientError:
            pass  # most likely a key id we have not seen: the provider may have rotated its keys
        with self._lock:  # one forced refresh per interval, so forged key ids cannot flood the provider
            now = self._clock()
            if self._last_refresh is not None and now - self._last_refresh < self.refresh_interval:
                raise AuthError("Unknown signing key")
            self._last_refresh = now
            try:
                self._client.get_jwk_set(refresh=True)
                return self._client.get_signing_key_from_jwt(token)
            except PyJWKClientConnectionError as exc:
                raise AuthUnavailable("Cannot reach the sign-in service's key server") from exc
            except PyJWKClientError as exc:
                raise AuthError("Unknown signing key") from exc

    def verify(self, token: str) -> dict:
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise AuthError("Missing or oversized token")
        try:
            key = self._signing_key(token)
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
