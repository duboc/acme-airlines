"""`uv run acme-customer`: serve the runtime API (Customer agent + in-process simulators).

Two listeners share one runtime: the passenger-facing API (`ACME_CUSTOMER_PORT`, 8800) and the
A2A-only listener (`ACME_A2A_HOST`/`ACME_A2A_PORT`, 8802) that Sales is pointed at (#230).
"""

import asyncio
import os
from collections.abc import Mapping

import uvicorn

from acme.api.app import create_a2a_app, create_app
from acme.logging_setup import configure_logging
from acme.metrics import configure_cloud_monitoring
from acme.telemetry import configure_cloud_trace

SERVICE_NAME = "acme-irrops-demo"


def require_sales_token(env: Mapping[str, str]) -> None:
    """Fail closed: without the Sales credential the runtime cannot fence Sales (#230).

    `ENGINE_MODE=agent-engine` has no local Sales HTTP caller, so it is exempt.
    """
    if (env.get("ENGINE_MODE") or "local") != "agent-engine" and not env.get("ACME_SALES_TOKEN"):
        raise SystemExit(
            "ACME_SALES_TOKEN is required (the sales credential the runtime uses to fence Sales)"
        )


async def _serve(app, host: str, a2a_host: str, port: int, a2a_port: int) -> None:
    servers = [uvicorn.Server(uvicorn.Config(app, host=host, port=port))]
    runtime = getattr(app.state, "runtime", None)
    if runtime is not None:  # engine mode has no local runtime, so no local A2A listener
        a2a = create_a2a_app(runtime)
        servers.append(uvicorn.Server(uvicorn.Config(a2a, host=a2a_host, port=a2a_port)))
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    configure_logging()
    # No-ops unless ACME_ENABLE_TRACING (or the telemetry switch) is set: the 5xx counter of this
    # service is exported like the engines' metrics (#344).
    configure_cloud_trace(SERVICE_NAME)
    configure_cloud_monitoring(SERVICE_NAME)
    require_sales_token(os.environ)
    host = os.environ.get("ACME_CUSTOMER_HOST", "127.0.0.1")
    port = int(os.environ.get("ACME_CUSTOMER_PORT", "8800"))
    a2a_port = int(os.environ.get("ACME_A2A_PORT", "8802"))
    a2a_host = os.environ.get("ACME_A2A_HOST", host)  # compose binds each listener to one network
    asyncio.run(_serve(create_app(), host, a2a_host, port, a2a_port))
