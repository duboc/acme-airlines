"""`uv run acme-sales`: the Sales agent service (agent card + background radar loop).

Env: `ACME_CUSTOMER_URL` (Customer A2A base, default http://127.0.0.1:8800), `ACME_SIM_URL`
(default http://127.0.0.1:8700), `ACME_SALES_TOKEN` (REQUIRED: the `sales` credential; the agent
refuses to start without it), `ACME_SALES_PORT` (8801), `ACME_SALES_TICK_SECONDS` (5).
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request

from acme.sales.agent import sales_agent_card
from acme.sales.client import A2ACustomerClient
from acme.sales.radar import SalesRadar
from acme.sim.client import SimClient, http_connector

CARD_PATH = "/.well-known/agent-card.json"


def create_app(radar: SalesRadar, tick_seconds: float | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if tick_seconds:

            async def loop() -> None:
                while True:
                    try:
                        await radar.tick()
                    except Exception:  # noqa: BLE001 - keep the radar alive
                        pass
                    await asyncio.sleep(tick_seconds)

            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="ACME Sales agent", lifespan=lifespan)

    @app.get(CARD_PATH, include_in_schema=False)
    async def card(request: Request) -> dict:
        return sales_agent_card(str(request.base_url)).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

    return app


def main() -> None:
    token = os.environ.get("ACME_SALES_TOKEN", "")
    if not token:
        raise SystemExit("ACME_SALES_TOKEN is required (the sales credential for acme-sim)")
    customer = httpx.AsyncClient(
        base_url=os.environ.get("ACME_CUSTOMER_URL", "http://127.0.0.1:8800")
    )
    inventory = SimClient(
        http_connector(
            os.environ.get("ACME_SIM_URL", "http://127.0.0.1:8700"), "sales", {"sales": token}
        )
    )
    app = create_app(
        SalesRadar(A2ACustomerClient(customer), inventory),
        float(os.environ.get("ACME_SALES_TICK_SECONDS", "5")),
    )
    uvicorn.run(
        app,
        host=os.environ.get("ACME_SALES_HOST", "127.0.0.1"),
        port=int(os.environ.get("ACME_SALES_PORT", "8801")),
    )
