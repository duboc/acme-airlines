"""`uv run acme-customer`: serve the runtime API (Customer agent + in-process simulators).

Two listeners share one runtime: the passenger-facing API (`ACME_CUSTOMER_PORT`, 8800) and the
A2A-only listener (`ACME_A2A_PORT`, 8802) that Sales is pointed at (#230).
"""

import asyncio
import os

import uvicorn

from acme.api.app import create_a2a_app, create_app
from acme.logging_setup import configure_logging


async def _serve(app, host: str, a2a_host: str, port: int, a2a_port: int) -> None:
    servers = [uvicorn.Server(uvicorn.Config(app, host=host, port=port))]
    runtime = getattr(app.state, "runtime", None)
    if runtime is not None:  # engine mode has no local runtime, so no local A2A listener
        a2a = create_a2a_app(runtime)
        servers.append(uvicorn.Server(uvicorn.Config(a2a, host=a2a_host, port=a2a_port)))
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    configure_logging()
    host = os.environ.get("ACME_CUSTOMER_HOST", "127.0.0.1")
    port = int(os.environ.get("ACME_CUSTOMER_PORT", "8800"))
    a2a_port = int(os.environ.get("ACME_A2A_PORT", "8802"))
    asyncio.run(_serve(create_app(), host, host, port, a2a_port))
