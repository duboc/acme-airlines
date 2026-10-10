from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from acme.sim.app import create_app
from acme.sim.world import SimWorld


@pytest.fixture
def sim() -> SimWorld:
    return SimWorld.seeded()


@pytest.fixture
def connect(sim):
    """`async with connect("occ") as session` talks MCP to the in-process app."""

    @asynccontextmanager
    async def _connect(server: str, headers: dict[str, str] | None = None):
        # One app (and lifespan) per connection, all over the same shared `sim` world.
        app = create_app(sim)
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://acme-sim", headers=headers
        )
        async with app.router.lifespan_context(app), http:
            async with streamable_http_client(
                f"http://acme-sim/mcp/{server}/", http_client=http
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    return _connect


async def call(session: ClientSession, tool: str, **args) -> dict:
    result = await session.call_tool(tool, args)
    assert not result.isError, result.content
    return result.structuredContent
