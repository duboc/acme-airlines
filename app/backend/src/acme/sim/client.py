"""MCP client for the simulated systems: in-process (tests, demo) or over HTTP.

A `Connector` opens a short-lived MCP session to one server. Each connector
acts as ONE caller and presents only that caller's token (`acme.sim.auth.client_config`, #27).
"""

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP

from acme.sim.app import DEFAULT_REGISTRY, ServerFactory, create_app
from acme.sim.auth import client_config, load_tokens
from acme.sim.world import SimWorld


@dataclass
class _Reuse:
    stack: AsyncExitStack
    sessions: dict[tuple[int, str], ClientSession] = field(default_factory=dict)


_REUSE: ContextVar[_Reuse | None] = ContextVar("acme_sim_session_reuse", default=None)


@asynccontextmanager
async def reuse_sessions():
    """In-process connectors used inside this block keep ONE session per (connector, server)
    open instead of one per call (#280: a replay makes ~70 calls). All sessions are closed when
    the block exits, in the task that opened them. Nothing outlives the block; the HTTP
    connector is unaffected. Use from a single task."""
    stack, failure = AsyncExitStack(), None
    token = _REUSE.set(_Reuse(stack))
    try:
        yield
    except BaseException as exc:  # noqa: BLE001 - re-raised below, after a clean close
        failure = exc
    finally:
        _REUSE.reset(token)
        await stack.aclose()  # close without the body's error: anyio would wrap it in a group
    if failure is not None:
        raise failure


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
    servers: dict[str, FastMCP] = {}  # one tool registry per server, bound to THIS `sim` only

    def registry_for(server: str) -> ServerFactory:
        def factory(world: SimWorld) -> FastMCP:
            if server not in servers:  # registering the tools is the costly part (#280)
                servers[server] = DEFAULT_REGISTRY[server](world)
            mcp = servers[server]
            mcp._session_manager = None  # noqa: SLF001 - a session manager runs once per instance
            return mcp

        return factory

    @asynccontextmanager
    async def open_session(server: str):
        # Mount only the requested server: a session manager runs once per instance.
        app = create_app(sim, registry={server: registry_for(server)}, tokens=tokens)
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://acme-sim",
            headers=caller_headers(caller, "http://acme-sim", tokens, server),
        )
        async with app.router.lifespan_context(app), _session(http, server) as session:
            yield session

    @asynccontextmanager
    async def connect(server: str):
        reuse = _REUSE.get()
        if reuse is None:  # the normal path: one short-lived session per call
            async with open_session(server) as session:
                yield session
            return
        key = (id(connect), server)  # this connector (one run, one caller) and server only
        if key not in reuse.sessions:
            reuse.sessions[key] = await reuse.stack.enter_async_context(open_session(server))
        try:
            yield reuse.sessions[key]
        except BaseException:
            reuse.sessions.pop(key, None)  # never reuse a session that failed: reopen next call
            raise

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
