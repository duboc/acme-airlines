"""FastAPI app. GET-only: FastAPI answers any other method on these routes with 405."""

import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from . import service
from .gcp import GcpDataSource
from .models import (
    AgentEngineResponse,
    AgentsResponse,
    BudgetResponse,
    CollectorHealthResponse,
    GithubResponse,
    IrropsResponse,
    ModelsResponse,
    SpendResponse,
)
from .source import DataSource, FakeDataSource

PROJECT_DEFAULT = "acme-airlines-factory"
DEFAULT_STATIC = Path(__file__).resolve().parents[2] / "web" / "dist"


def select_source(env: dict | os._Environ = os.environ) -> DataSource:
    """OBSERVATORY_DATA=fake -> fixtures; anything else (unset in production) -> GCP."""
    if env.get("OBSERVATORY_DATA") == "fake":
        return FakeDataSource(env.get("OBSERVATORY_SCENARIO", "ok"))
    return GcpDataSource.from_env()


def create_app(
    source: DataSource | None = None,
    budget_usd: float | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    source = source or select_source()
    budget = budget_usd if budget_usd is not None else float(os.environ.get("BUDGET_USD", "1000"))
    app = FastAPI(title="ACME Observatory", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    @app.get("/api/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/api/overview")
    def overview() -> CollectorHealthResponse:
        return service.collector_health(source)

    @app.get("/api/spend")
    def spend(days: int = Query(30, ge=1, le=365)) -> SpendResponse:
        return service.spend(source, days)

    @app.get("/api/models")
    def models(hours: int = Query(24, ge=1, le=720)) -> ModelsResponse:
        return service.models(source, hours)

    @app.get("/api/agents")
    def agents() -> AgentsResponse:
        return service.agents(source)

    @app.get("/api/github")
    def github() -> GithubResponse:
        return service.github(source)

    @app.get("/api/budget")
    def budget_() -> BudgetResponse:
        return service.budget(source, budget)

    @app.get("/api/collector-health")
    def collector_health() -> CollectorHealthResponse:
        return service.collector_health(source)

    @app.get("/api/agent-engine")
    def agent_engine(hours: int = Query(1, ge=1, le=168)) -> AgentEngineResponse:
        return service.agent_engine(source, hours, os.environ.get("PROJECT_ID", PROJECT_DEFAULT))

    @app.get("/api/irrops")
    def irrops(hours: int = Query(24, ge=1, le=168)) -> IrropsResponse:
        return service.irrops(source, hours)

    static = (
        static_dir
        if static_dir is not None
        else Path(os.environ.get("DASHBOARD_STATIC") or DEFAULT_STATIC)
    )
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    return app
