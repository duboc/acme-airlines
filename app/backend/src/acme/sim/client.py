"""MCP client for the simulated systems: in-process (tests, demo) or over HTTP.

A `Connector` opens a short-lived MCP session to one server. Each connector
acts as ONE caller and presents only that caller's token (`acme.sim.auth.client_config`, #27).
"""

import json
import logging
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx
from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from referencing import Registry

from acme.sim.app import DEFAULT_REGISTRY, ServerFactory, create_app
from acme.sim.auth import client_config, load_tokens
from acme.sim.current import CURRENT, WorldRef
from acme.sim.world import SimWorld

log = logging.getLogger(__name__)
_SHARED: dict[str, FastMCP] = {}  # one tool registry per server for the whole process (#280)
_SHARED_LOCK = threading.Lock()


def _shared_server(server: str) -> ServerFactory:
    """A factory for the process-wide FastMCP of `server`: built once against a `WorldRef`, so it
    holds no world. Only its session manager is per use (a manager runs once per instance)."""

    def factory(_world: SimWorld) -> FastMCP:
        with _SHARED_LOCK:
            if server not in _SHARED:
                _SHARED[server] = DEFAULT_REGISTRY[server](WorldRef())  # type: ignore[arg-type]
            mcp = _SHARED[server]
            mcp._session_manager = None  # noqa: SLF001 - a session manager runs once per instance
            return mcp

    return factory


def warm_registry() -> None:
    """Build every shared tool registry now (engine start-up), so no rebuild pays for it."""
    for server in DEFAULT_REGISTRY:
        _shared_server(server)(None)  # type: ignore[arg-type]


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


_VALIDATORS: dict[tuple[str, str], Any] = {}  # (tool, schema as JSON) -> checked validator


def _validator(name: str, schema: dict[str, Any]) -> Any:
    """The validator of a tool's output schema, schema-checked once per process (#438)."""
    key = (name, json.dumps(schema, sort_keys=True))
    if key not in _VALIDATORS:
        cls = validator_for(schema)
        try:
            cls.check_schema(schema)
        except SchemaError as exc:
            raise RuntimeError(f"Invalid schema for tool {name}: {exc}") from exc
        _VALIDATORS[key] = cls(schema, registry=Registry())
    return _VALIDATORS[key]


class _CachingSession(ClientSession):
    """A `ClientSession` that checks a tool's output schema once, not once per call.

    The SDK validates every result with `jsonschema.validate`, which re-checks the schema against
    its metaschema each time: about a quarter of a 400 ms rebuild (#438). The result is validated
    exactly as before (same schema, same errors); only the repeated schema check is gone."""

    _checked: dict[str, Any]

    async def _validate_tool_result(self, name: str, result: CallToolResult) -> None:
        if name not in self._tool_output_schemas:
            await self.list_tools()  # refresh the output schema cache
        schema = self._tool_output_schemas.get(name)
        if schema is None:
            log.warning("Tool %s has no output schema, cannot validate structured content", name)
            return
        if result.structuredContent is None:
            raise RuntimeError(f"Tool {name} has an output schema but did not return content")
        if not hasattr(self, "_checked"):
            self._checked = {}
        if name not in self._checked:
            self._checked[name] = _validator(name, schema)
        try:
            self._checked[name].validate(result.structuredContent)
        except ValidationError as exc:
            raise RuntimeError(
                f"Invalid structured content returned by tool {name}: {exc}"
            ) from exc


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
        async with _CachingSession(read, write) as session:
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
    async def open_session(server: str):
        # Mount only the requested server: a session manager runs once per instance.
        app = create_app(sim, registry={server: _shared_server(server)}, tokens=tokens)
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://acme-sim",
            headers=caller_headers(caller, "http://acme-sim", tokens, server),
        )
        token = CURRENT.set(sim)  # the server tasks created below inherit THIS run's world
        try:
            async with app.router.lifespan_context(app), _session(http, server) as session:
                yield session
        finally:
            CURRENT.reset(token)

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
