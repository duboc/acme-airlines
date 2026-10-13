"""Generic API errors (#259): the response never echoes the identifier the caller sent.

The offending id stays in the server log (redacted by `acme.logging_setup`); the client gets a
fixed message and a stable `code` next to FastAPI's `detail`.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("acme.api")


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, *, log_detail: str = "") -> None:
        super().__init__(status, message)
        self.code = code
        if log_detail:
            log.info("%s: %s", code, log_detail)


def not_found(what: str, ident: str) -> ApiError:
    return ApiError(404, f"{what}_not_found", f"{what} not found", log_detail=f"{what} {ident}")


def add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, e: ApiError) -> JSONResponse:
        return JSONResponse({"detail": e.detail, "code": e.code}, status_code=e.status_code)
