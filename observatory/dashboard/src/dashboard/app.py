"""FastAPI app. GET-only: FastAPI answers any other method on these routes with 405."""

import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from . import service
from .models import (
    AgentsResponse,
    BudgetResponse,
    GithubResponse,
    ModelsResponse,
    SpendResponse,
)
from .source import DataSource, FakeDataSource

DEFAULT_STATIC = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(
    source: DataSource | None = None,
    budget_usd: float | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    if source is None:
        if os.environ.get("OBSERVATORY_DATA") != "fake":
            raise RuntimeError("no live data source yet (slice 4b); set OBSERVATORY_DATA=fake")
        source = FakeDataSource(os.environ.get("OBSERVATORY_SCENARIO", "ok"))
    budget = budget_usd if budget_usd is not None else float(os.environ.get("BUDGET_USD", "1000"))
    app = FastAPI(title="ACME Observatory", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

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

    static = (
        static_dir
        if static_dir is not None
        else Path(os.environ.get("DASHBOARD_STATIC") or DEFAULT_STATIC)
    )
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    return app
