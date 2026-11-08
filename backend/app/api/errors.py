"""One error shape for every failure: {"error": {"code": str, "message": str}}. Messages are
short and generic; stack traces and internals only go to the logs."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.conversation import ConversationError

_HTTP_CODES = {400: "invalid_request", 404: "not_found", 405: "method_not_allowed"}


class ApiError(ConversationError):
    """An error raised by a route or dependency, with its own status, code and headers (for
    example WWW-Authenticate on 401, Retry-After on 429)."""

    def __init__(
        self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.headers = headers


def error_response(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(ConversationError)
    async def _conversation(_: Request, exc: ConversationError) -> JSONResponse:
        headers = exc.headers if isinstance(exc, ApiError) else None
        return error_response(exc.status_code, exc.code, exc.message, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted(
            "body" if e["type"] == "json_invalid" else ".".join(map(str, e["loc"][1:])) or "body"
            for e in exc.errors()
        )
        fields = list(dict.fromkeys(fields))
        return error_response(400, "invalid_request", f"Invalid request: {', '.join(fields)}.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Starlette re-raises after this response, so the server logs the traceback once.
        return error_response(500, "internal_error", "Something went wrong. Try again.")
