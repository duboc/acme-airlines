"""MCP client for the simulated systems: in-process (tests, demo) or over HTTP.

A `Connector` opens a short-lived MCP session to one server. Per-caller credentials
(#27) plug in later through `http_connector(headers=...)`; nothing else needs to change.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from acme.sim.app import DEFAULT_REGISTRY, create_app
from acme.sim.world import SimWorld

Connector = Callable[[str], AbstractAsyncContextManager[ClientSession]]


class ToolError(RuntimeError):
    """An MCP tool returned an error result."""


@asynccontextmanager
async def _session(http: httpx.AsyncClient, server: str):
    async with (
        http,
        streamable_http_client(f"{http.base_url}/mcp/{server}/", http_client=http) as (
            read,
            write,
            _,
        ),
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def inprocess_connector(sim: SimWorld) -> Connector:
    @asynccontextmanager
    async def connect(server: str):
        # Mount only the requested server: a session manager runs once per instance.
        app = create_app(sim, registry={server: DEFAULT_REGISTRY[server]})
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acme-sim")
        async with app.router.lifespan_context(app), _session(http, server) as session:
            yield session

    return connect


def http_connector(base_url: str, headers: dict[str, str] | None = None) -> Connector:
    @asynccontextmanager
    async def connect(server: str):
        http = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers)
        async with _session(http, server) as session:
            yield session

    return connect


class SimClient:
    def __init__(self, connector: Connector) -> None:
        self._connect = connector

    async def call(self, server: str, tool: str, **args: Any) -> dict[str, Any]:
        async with self._connect(server) as session:
            result = await session.call_tool(tool, args)
        if result.isError:
            raise ToolError(f"{server}/{tool}: {result.content}")
        return result.structuredContent or {}
