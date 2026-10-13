"""Runtime enforcement that the Sales agent reaches the Customer runtime through `/a2a` only.

Sales identifies itself with its per-caller credential (`Authorization: Bearer <sales token>`).
A request carrying that credential is allowed to `POST /a2a` and nothing else: every other path
(WhatsApp, scenario reset, journeys, ...) is refused with 403 and an audit event. Fail closed:
no path normalisation tricks, only the exact `/a2a` path and method qualify. The credential is
never logged. `ACME_SALES_TOKEN` configures the token (same value Sales presents).
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Mapping

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from acme import metrics

audit = logging.getLogger("acme.audit")
A2A_PATH = "/a2a"


def sales_token_from_env(env: Mapping[str, str]) -> str | None:
    return env.get("ACME_SALES_TOKEN") or None


def _is_sales(scope: Scope, token: str) -> bool:
    """Fail closed: the Sales credential in ANY `Authorization` header, in ANY position or
    spacing (extra spaces/tabs, duplicate headers, odd schemes), marks the caller as Sales."""
    wanted = token.encode()
    found = False
    for key, value in scope["headers"]:  # no early exit: constant-time over every part
        if key.lower() == b"authorization":
            for part in value.split():
                found |= hmac.compare_digest(part, wanted)
    return found


class SalesRouteGuard:
    def __init__(self, app: ASGIApp, sales_token: str | None) -> None:
        self.app, self._token = app, sales_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._token and scope["type"] in ("http", "websocket") and _is_sales(scope, self._token):
            if not (
                scope["type"] == "http" and scope["path"] == A2A_PATH and scope["method"] == "POST"
            ):
                audit.warning(
                    "guardrail audit: sales caller denied route=%s method=%s",
                    scope["path"],
                    scope.get("method", "WS"),
                    extra={"event": "sales_route_denied", "caller": "sales"},
                )
                metrics.guardrail_decision(False, ["sales_route_denied"], "route")
                return await PlainTextResponse("forbidden", 403)(scope, receive, send)
        await self.app(scope, receive, send)
