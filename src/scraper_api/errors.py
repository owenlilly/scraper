"""One error shape for every failure: {"error": {"code": ..., "message": ..., ...}}."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, headers: dict[str, str] | None = None, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.extra = extra


def error_body(code: str, message: str, **extra) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(error_body(exc.code, exc.message, **exc.extra), status_code=exc.status, headers=exc.headers)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Name the field and the rule, never the value: it may be a secret or a whole pasted page.
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
    return JSONResponse(error_body("invalid", f"{where or 'request'}: {first.get('msg', 'is not valid')}"), status_code=422)
