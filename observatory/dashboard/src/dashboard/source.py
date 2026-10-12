"""Data sources. Raw records in, the service layer decides state and shape.

The real GCP source (slice 4b) implements the same protocol. Raw record shapes:
billing ``{usage_date, service, cost_usd}``; monitoring ``{model_id, tokens, requests}``;
agent/github snapshot rows as in ``collector/schema.py``; guard ``{ts, text}``;
merges ``{date, count}``. A method may raise ``SourceUnavailable`` or ``SourceEmpty``
(both carry the message shown to the UI).
"""

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

Rows = list[dict]


class SourceUnavailable(Exception):
    """The backing system cannot be read (e.g. billing export not enabled yet)."""


class SourceEmpty(Exception):
    """Nothing to read yet, with a reason for the UI (e.g. billing export not enabled)."""


class TraceNotGranted(Exception):
    """The dashboard SA may not read Cloud Trace (needs ``cloudtrace.user``, issue #158)."""


class DataSource(Protocol):
    def now(self) -> datetime: ...
    def billing_rows(self, start: date) -> Rows: ...
    def monitoring_rows(self, hours: int) -> Rows: ...
    def agent_snapshots(self) -> Rows: ...
    def github_snapshots(self) -> Rows: ...
    def merges_per_day(self, days: int) -> Rows: ...
    def guard_logs(self, limit: int) -> Rows: ...
    def agent_engines(self) -> Rows: ...  # [{name, engine_id}]
    def agent_engine_metrics(self, engine_id: str, hours: int) -> dict: ...
    def agent_engine_traces(self, name: str, limit: int) -> Rows: ...  # may raise TraceNotGranted


FIXTURES = Path(
    os.environ.get("OBSERVATORY_FIXTURES") or Path(__file__).resolve().parents[3] / "fixtures"
)
FAKE_NOW = datetime(2026, 10, 10, 12, 0, 30, tzinfo=UTC)
SCENARIOS = ("ok", "empty", "lagging", "unavailable")


class FakeDataSource:
    """Reads ``observatory/fixtures/``. ``scenario`` picks a state:

    ok: the fixtures; empty: no rows; lagging: same rows, clock moved 3 days ahead;
    unavailable: every read raises SourceUnavailable.
    """

    def __init__(self, scenario: str = "ok", fixtures: Path = FIXTURES) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; use one of {SCENARIOS}")
        self.scenario = scenario
        self._dir = fixtures

    def now(self) -> datetime:
        return FAKE_NOW + timedelta(days=3) if self.scenario == "lagging" else FAKE_NOW

    def _read(self, name: str) -> Rows:
        if self.scenario == "unavailable":
            raise SourceUnavailable("fake scenario: source unavailable")
        if self.scenario == "empty":
            return []
        return json.loads((self._dir / f"{name}.json").read_text())

    def billing_rows(self, start: date) -> Rows:
        return [r for r in self._read("billing") if r["usage_date"] >= start.isoformat()]

    def monitoring_rows(self, hours: int) -> Rows:
        return self._read("monitoring")

    def agent_snapshots(self) -> Rows:
        return self._read("agent_snapshots")

    def github_snapshots(self) -> Rows:
        return self._read("github_snapshots")

    def merges_per_day(self, days: int) -> Rows:
        return self._read("merges_per_day")[-days:]

    def guard_logs(self, limit: int) -> Rows:
        return self._read("guard_logs")[-limit:]

    def _engine_data(self) -> dict:
        data = self._read("agent_engine")
        return data or {"engines": [], "metrics": {}, "traces": {}}

    def agent_engines(self) -> Rows:
        return self._engine_data()["engines"]

    def agent_engine_metrics(self, engine_id: str, hours: int) -> dict:
        return self._engine_data()["metrics"][engine_id]

    def agent_engine_traces(self, name: str, limit: int) -> Rows:
        return self._engine_data()["traces"].get(name, [])[:limit]
