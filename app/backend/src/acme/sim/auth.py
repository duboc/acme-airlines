"""Server-side isolation of `acme-sim` (governance layer 1): per-caller bearer tokens and a
deny-by-default server allowlist.

- Every HTTP request needs `Authorization: Bearer <token>`. Missing/unknown -> 401.
- The token maps to a caller; the caller's allowlist is checked BEFORE routing, so a path the
  caller may not use is refused with 403 whether or not a server is mounted there.
- `sales` may reach `inventory` only. `customer` may reach every server (incl. read-only
  `inventory`). `test-admin` is for tests/demo only.
- Tokens are never logged or echoed. Denials log caller and server, not credentials.

Tokens come from `ACME_SIM_TOKENS="customer=<tok>,sales=<tok>,test-admin=<tok>"`. When unset
they are generated randomly per process (never committed, never printed).
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from collections.abc import Iterable, Mapping
from typing import Any

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("acme.sim.auth")

ALL = "*"
ALLOWLIST: dict[str, frozenset[str] | str] = {
    "customer": ALL,
    "sales": frozenset({"inventory"}),
    "test-admin": ALL,
}
MIN_TOKEN_LEN = 12
_SERVER_PATH = re.compile(r"^/mcp/([a-z0-9_-]+)(?:/[^/]+)*/?$")


def load_tokens(env: Mapping[str, str]) -> dict[str, str]:
    """Parse ACME_SIM_TOKENS, or generate random tokens for every caller if unset."""
    raw = env.get("ACME_SIM_TOKENS", "").strip()
    if not raw:
        return {caller: secrets.token_urlsafe(32) for caller in ALLOWLIST}
    tokens: dict[str, str] = {}
    for pair in raw.split(","):
        caller, sep, token = pair.strip().partition("=")
        if not sep or caller not in ALLOWLIST:
            raise ValueError("ACME_SIM_TOKENS entries must be <known-caller>=<token>")
        if len(token) < MIN_TOKEN_LEN:
            raise ValueError(f"token for {caller!r} is shorter than {MIN_TOKEN_LEN} characters")
        tokens[caller] = token
    if len(set(tokens.values())) != len(tokens):
        raise ValueError("ACME_SIM_TOKENS callers must have distinct tokens")
    return tokens


def _caller_for(headers: Iterable[tuple[bytes, bytes]], tokens: Mapping[str, str]) -> str | None:
    value = next((v for k, v in headers if k.lower() == b"authorization"), b"").decode("latin-1")
    scheme, _, presented = value.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return None
    found = None
    for caller, token in tokens.items():  # no early exit: constant-time over all callers
        if hmac.compare_digest(presented.encode(), token.encode()):
            found = caller
    return found


def _allowed(caller: str, path: str) -> bool:
    allow = ALLOWLIST.get(caller, frozenset())
    if ".." in path.split("/") or "//" in path or "%" in path:
        return False
    if allow == ALL:
        return True
    m = _SERVER_PATH.match(path)
    return bool(m) and m.group(1) in allow


class AccessControlMiddleware:
    def __init__(self, app: ASGIApp, tokens: Mapping[str, str]) -> None:
        self.app = app
        self._tokens = dict(tokens)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan (and websocket: not served) pass through
            if scope["type"] == "websocket":
                return await PlainTextResponse("forbidden", 403)(scope, receive, send)
            return await self.app(scope, receive, send)
        caller = _caller_for(scope["headers"], self._tokens)
        if caller is None:
            log.warning("sim auth: rejected request without a valid credential")
            return await PlainTextResponse("unauthorized", 401)(scope, receive, send)
        if not _allowed(caller, scope["path"]):
            log.warning("sim auth: caller=%s denied path=%s", caller, scope["path"])
            return await PlainTextResponse("forbidden", 403)(scope, receive, send)
        await self.app(scope, receive, send)


def client_config(
    caller: str, base_url: str, tokens: Mapping[str, str], servers: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """MCP client config for `caller`: only the servers its allowlist permits, with its token.

    Used by the Customer runtime (#10) and Sales (#14) so each holds exactly its own credential.
    Raises KeyError for an unknown caller or one without a token.
    """
    token = tokens[caller]
    allow = ALLOWLIST[caller]
    base = base_url.rstrip("/")
    return {
        name: {"url": f"{base}/mcp/{name}/", "headers": {"Authorization": f"Bearer {token}"}}
        for name in servers
        if allow == ALL or name in allow
    }
