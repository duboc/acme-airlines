"""Front proxy for the single-container demo: static frontend + `/api/*` -> the runtime.

Agent-to-agent (`a2a`, `.well-known`) and MCP (`mcp`) paths are never reachable from outside:
they answer 404 whether at the root or under `/api`, after normalising case, slashes and dots.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse
from starlette.routing import Route

BLOCKED_PREFIXES = ("/api/a2a", "/api/.well-known", "/api/mcp")
_BLOCKED_SEGMENTS = {"a2a", ".well-known", "mcp"}
_HOP = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "host",
    "content-length",
    "content-encoding",
}


def _segments(path: str) -> list[str]:
    return [s for s in unquote(unquote(path)).split("/") if s]


def _blocked(segs: list[str]) -> bool:
    segs = [s.lower() for s in segs]
    return bool(segs) and (
        segs[0] in _BLOCKED_SEGMENTS
        or (segs[0] == "api" and len(segs) > 1 and segs[1] in _BLOCKED_SEGMENTS)
    )


def create_front(static_dir: Path, upstream: httpx.AsyncClient) -> Starlette:
    root = Path(static_dir).resolve()

    async def handle(request: Request) -> Response:
        segs = _segments(request.url.path)
        if _blocked(segs):
            return Response("Not Found", status_code=404)
        if segs and segs[0] == "api":
            return await _proxy(request, upstream, "/" + "/".join(segs[1:]))
        candidate = (root / "/".join(request.url.path.lstrip("/").split("/"))).resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(root / "index.html")

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    return Starlette(routes=[Route("/{path:path}", handle, methods=methods)])


async def _proxy(request: Request, upstream: httpx.AsyncClient, path: str) -> Response:
    # `path` is rebuilt from decoded, de-duplicated segments (case preserved).
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    req = upstream.build_request(
        request.method,
        path,
        params=request.query_params,
        headers=headers,
        content=await request.body(),
    )
    resp = await upstream.send(req, stream=True)
    out = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP}

    async def body():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(body(), status_code=resp.status_code, headers=out)
