"""MCP client for the simulated systems: in-process (tests, demo) or over HTTP.

A `Connector` opens a short-lived MCP session to one server. Each connector
acts as ONE caller and presents only that caller's token (`acme.sim.auth.client_config`, #27).
"""

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from acme.sim.app import DEFAULT_REGISTRY, create_app
from acme.sim.auth import client_config, load_tokens
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


def caller_headers(
    caller: str, base_url: str, tokens: Mapping[str, str], server: str
) -> dict[str, str]:
    cfg = client_config(caller, base_url, tokens, [server])  # per-caller helper (#27)
    if server not in cfg:  # the server would answer 403 anyway; fail early and clearly
        raise ToolError(f"caller {caller!r} is not allowed to use server {server!r}")
    return cfg[server]["headers"]


def inprocess_connector(
    sim: SimWorld, caller: str = "customer", tokens: Mapping[str, str] | None = None
) -> Connector:
    """In-process sim with the middleware ON; `caller` presents only its own credential."""
    tokens = dict(tokens) if tokens else load_tokens({})  # random per connector when not given

    @asynccontextmanager
    async def connect(server: str):
        # Mount only the requested server: a session manager runs once per instance.
        app = create_app(sim, registry={server: DEFAULT_REGISTRY[server]}, tokens=tokens)
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://acme-sim",
            headers=caller_headers(caller, "http://acme-sim", tokens, server),
        )
        async with app.router.lifespan_context(app), _session(http, server) as session:
            yield session

    return connect


def http_connector(base_url: str, caller: str, tokens: Mapping[str, str]) -> Connector:
    """Remote `acme-sim`: each connection carries `caller`'s token (see `client_config`)."""

    @asynccontextmanager
    async def connect(server: str):
        http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=caller_headers(caller, base_url, tokens, server)
        )
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
