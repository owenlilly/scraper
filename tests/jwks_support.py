"""A throwaway identity provider for tests: signs EdDSA tokens and serves their public keys as a JWKS."""

import http.server
import json
import threading
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ISSUER = "https://web.test"
AUDIENCE = "https://api.test"


class Signer:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self._private = Ed25519PrivateKey.generate()
        self.jwk = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(self._private.public_key()))
        self.jwk["kid"] = kid

    def token(self, sub: str | None = "user-1", *, kid: str | None = None, **overrides) -> str:
        claims = {"sub": sub, "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 900, **overrides}
        claims = {k: v for k, v in claims.items() if v is not None}
        pem = self._private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        return jwt.encode(claims, pem, algorithm="EdDSA", headers={"kid": kid or self.kid})


class JWKSServer:
    """Serves whichever keys `keys` currently holds and counts how often it was asked."""

    def __init__(self, *signers: Signer) -> None:
        self.keys = [s.jwk for s in signers]
        self.requests = 0
        self.down = False
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests += 1
                if outer.down:
                    self.send_response(503)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"keys": outer.keys}).encode())

            def log_message(self, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_port}/api/auth/jwks"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
