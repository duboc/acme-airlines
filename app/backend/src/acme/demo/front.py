"""Front proxy for the single-container demo: static frontend + `/api/*` -> the runtime.

Agent-to-agent (`a2a`, `.well-known`) and MCP (`mcp`) paths are never reachable from outside:
they answer 404 whether at the root or under `/api`, after normalising case, slashes and dots.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import unquote

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse
from starlette.routing import Route

DISCONNECT_POLL_SECONDS = 1.0
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
    prev = None
    while path != prev:
        prev, path = path, unquote(path)
    out: list[str] = []
    for s in path.split("/"):
        if not s or s == ".":
            continue
        if s == "..":
            if out:
                out.pop()
            continue
        out.append(s)
    return out


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
        # Waiting for the next chunk of a stream must not hide that the browser is gone: look at
        # the client every second and close the upstream (the engine stops polling) (#305).
        chunks = resp.aiter_bytes().__aiter__()
        pending = None
        try:
            while True:
                pending = asyncio.ensure_future(chunks.__anext__())
                while not pending.done():
                    await asyncio.wait({pending}, timeout=DISCONNECT_POLL_SECONDS)
                    if not pending.done() and await request.is_disconnected():
                        return
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    return
                pending = None
                yield chunk
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
            await resp.aclose()

    return StreamingResponse(body(), status_code=resp.status_code, headers=out)
