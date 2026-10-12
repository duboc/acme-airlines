"""Logic of the live Agent Engine trace proof (#167). No GCP imports at module level.

`run_proof` drives presets through an `EngineBackend`-shaped object, finds each turn's Cloud
Trace traces through an injectable `TraceSource`, and checks spans and PII. The engine and the
trace layers are injected so CI unit-tests this with fakes (`test_agent_engine_proof_logic.py`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

STRUCTURAL = ("scenario_id", "step_index", "total_steps", "done", "journey_status", "flight",
              "seat", "offers_sent", "offer_status", "charged", "needs_human")  # fmt: skip
DEFAULT_PRESETS = ("happy-path", "declines-upgrade", "unsatisfiable-request", "seat-taken",
                   "hotel-unavailable")  # fmt: skip  # presets 1-5
# Span kinds that must show up somewhere in the run (prefix match on the span name).
REQUIRED_SPANS = ("acme.customer.specialist.", "acme.guardrail.decision", "acme.a2a.",
                  "acme.sales.pitch")  # fmt: skip
EXTRA_PII = ("Can I leave after 10am?", "Perfect, thanks!")


@dataclass(frozen=True)
class Span:
    name: str
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Trace:
    trace_id: str
    spans: tuple[Span, ...]


class TraceSource(Protocol):
    async def list_traces(self, start: datetime, end: datetime) -> list[Trace]: ...


@dataclass
class Row:
    scenario: str
    turn: int
    trace_id: str


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    project: str = ""

    def markdown(self) -> str:
        link = "https://console.cloud.google.com/traces/list?tid={}&project={}"
        lines = ["| Scenario | Turn | Trace id | Console |", "|---|---|---|---|"]
        for r in self.rows:
            url = link.format(r.trace_id, self.project)
            lines.append(f"| {r.scenario} | {r.turn} | `{r.trace_id}` | [open]({url}) |")
        return "\n".join(lines) + "\n"


def fixture_pii() -> set[str]:
    """Personal values from the fixtures (names, PNRs, phones, loyalty ids) plus passenger text."""
    from acme.domain.fixtures import mariana_world

    world = mariana_world()
    values: set[str] = set(EXTRA_PII) | set(world.pnrs)
    for p in world.passengers.values():
        values |= {p.name, *p.name.split()}
        values |= {v for v in (p.phone, p.loyalty.member_id if p.loyalty else None) if v}
    return {v for v in values if len(v) >= 3}


def pii_failures(traces: list[Trace], pii: set[str]) -> list[str]:
    out = []
    for t in traces:
        for s in t.spans:
            for text in (s.name, *s.attrs.values()):
                for value in pii:
                    if value in text:
                        out.append(f"PII in span {s.name!r} of trace {t.trace_id} ({value[:2]}...)")
    return out


def missing_spans(traces: list[Trace]) -> list[str]:
    names = [s.name for t in traces for s in t.spans]
    return [p for p in REQUIRED_SPANS if not any(n.startswith(p) for n in names)]


def step_trace(traces: list[Trace], scenario_id: str) -> Trace | None:
    """The Customer `scenario_step` trace: its root span carries `acme.scenario`."""
    for t in traces:
        if any(s.attrs.get("acme.scenario") == scenario_id for s in t.spans):
            return t
    return None


def structural(state: dict[str, Any]) -> dict[str, Any]:
    return {k: state.get(k) for k in STRUCTURAL}


async def run_proof(
    backend: Any,
    local: Any,
    traces: TraceSource,
    presets: tuple[str, ...] = DEFAULT_PRESETS,
    *,
    project: str = "",
    pii: set[str] | None = None,
    ingest_wait: float = 120.0,
    poll: float = 10.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
) -> Report:
    report = Report(project=project)
    pii = fixture_pii() if pii is None else pii
    seen: list[Trace] = []
    for sid in presets:
        await backend.scenario_start(sid)
        want = await local.scenario_start(sid)
        turn = 0
        while not want["done"]:
            turn += 1
            start = now() - timedelta(seconds=5)
            got = await backend.scenario_step(sid)
            want = await local.scenario_step(sid)
            if structural(got) != structural(want):
                report.failures.append(f"{sid} turn {turn}: engine {structural(got)} != "
                                       f"local {structural(want)}")  # fmt: skip
            found, deadline = None, monotonic() + ingest_wait
            while True:  # Cloud Trace ingestion lags; poll until the step trace appears
                window = await traces.list_traces(start, now() + timedelta(seconds=5))
                found = step_trace(window, sid)
                if found or monotonic() >= deadline:
                    break
                await sleep(poll)
            if found is None:
                report.failures.append(f"{sid} turn {turn}: no Customer step trace in Cloud Trace")
                continue
            report.rows.append(Row(sid, turn, found.trace_id))
            seen.extend(window)
    report.failures += [f"missing span kind {m!r} in every trace of the run"
                        for m in missing_spans(seen)]  # fmt: skip
    report.failures += pii_failures(seen, pii)
    return report


class CloudTraceV1:
    """Cloud Trace v1 REST (`projects.traces.list`, COMPLETE view) with ADC. Never used in CI."""

    def __init__(self, project: str) -> None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/trace.readonly"])
        self._session = AuthorizedSession(creds)
        self.project = project

    async def list_traces(self, start: datetime, end: datetime) -> list[Trace]:
        return await asyncio.to_thread(self._list, start, end)

    def _list(self, start: datetime, end: datetime) -> list[Trace]:
        url = f"https://cloudtrace.googleapis.com/v1/projects/{self.project}/traces"
        params: dict[str, Any] = {"view": "COMPLETE", "pageSize": 100,
                                  "startTime": start.isoformat().replace("+00:00", "Z"),
                                  "endTime": end.isoformat().replace("+00:00", "Z")}  # fmt: skip
        out: list[Trace] = []
        while True:
            r = self._session.get(url, params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            for t in body.get("traces", []):
                spans = tuple(Span(s["name"], dict(s.get("labels", {}))) for s in t["spans"])
                out.append(Trace(t["traceId"], spans))
            if not body.get("nextPageToken"):
                return out
            params["pageToken"] = body["nextPageToken"]
