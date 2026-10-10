"""`acme-sim`: all simulated MCP servers mounted in one ASGI app over one shared world."""

import os
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount

from acme.sim import partners, servers
from acme.sim.world import SimWorld

ServerFactory = Callable[[SimWorld], FastMCP]

# name -> factory. Add entries (and wrap the app in middleware) to extend the sim.
DEFAULT_REGISTRY: dict[str, ServerFactory] = {
    "occ": servers.build_occ,
    "pss": servers.build_pss,
    "crm": servers.build_crm,
    "inventory": servers.build_inventory,
    "concierge": partners.build_concierge,
    "whatsapp": partners.build_whatsapp,
    "payments": partners.build_payments,
}


def create_app(sim: SimWorld, registry: Mapping[str, ServerFactory] | None = None) -> Starlette:
    built = {name: make(sim) for name, make in (registry or DEFAULT_REGISTRY).items()}

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with AsyncExitStack() as stack:
            for mcp in built.values():
                await stack.enter_async_context(mcp.session_manager.run())
            yield

    routes = [Mount(f"/mcp/{name}", app=mcp.streamable_http_app()) for name, mcp in built.items()]
    return Starlette(routes=routes, lifespan=lifespan)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("ACME_SIM_PORT", "8700"))
    uvicorn.run(create_app(SimWorld.seeded()), host="127.0.0.1", port=port)
