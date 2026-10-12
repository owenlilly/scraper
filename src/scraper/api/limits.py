"""Refuse oversized request bodies before anything reads them.

FastAPI reads and parses a whole JSON body before it runs the dependency that checks the token, and neither it nor
uvicorn caps the size. Without this, anyone who knows the address could make the single process that holds every user's
session buffer a multi-gigabyte body. The largest legitimate body here is a few tens of kilobytes."""

import json

from .errors import error_body

MAX_BODY_BYTES = 64 * 1024
_REFUSAL = json.dumps(error_body("too_large", "That request is too large.")).encode()


class BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Pure ASGI, so it can refuse on the declared Content-Length and cut off a body that arrives without one."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _refuse(self, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(_REFUSAL)).encode()), (b"connection", b"close")],
            }
        )
        await send({"type": "http.response.body", "body": _REFUSAL})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._refuse(send)
            return

        received = 0
        cut_off = False

        async def limited_receive():
            nonlocal received, cut_off
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    cut_off = True
                    raise BodyTooLarge
            return message

        async def guarded_send(message):
            if not cut_off:  # once the body is cut off, whatever the app tries to say next (FastAPI turns the error into a 400) is dropped
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except BodyTooLarge:
            pass
        except Exception:
            if not cut_off:
                raise
        if cut_off:
            await self._refuse(send)
