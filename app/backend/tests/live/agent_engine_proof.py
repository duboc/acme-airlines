"""Logic of the live Agent Engine trace proof (#167). No GCP imports at module level.

`run_proof` drives presets through an `EngineBackend`-shaped object, finds each turn's Cloud
Trace traces through an injectable `TraceSource`, and checks spans and PII. The engine and the
trace layers are injected so CI unit-tests this with fakes (`test_agent_engine_proof_logic.py`).
"""

from __future__ import annotations

import asyncio
import random
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
POLL_GROWTH, POLL_MAX = 1.5, 45.0  # seconds: 15 -> 22.5 -> 33.75 -> 45 ...
# Cloud Trace v1 `filter`: `span:PREFIX` keeps traces with a span whose name starts with PREFIX
# (`+span:NAME` would require an exact name). Terms are ANDed. Syntax:
# https://cloud.google.com/trace/docs/trace-filters
SPAN_FILTER_PREFIX = "acme."
PAGE_SIZE, MAX_PAGES = 100, 10  # worst case 1000 traces per listing, then TraceListTruncated
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
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


def step_trace(
    traces: list[Trace], scenario_id: str, exclude: frozenset[str] | set[str] = frozenset()
) -> Trace | None:
    """The first not-yet-claimed Customer `scenario_step` trace (root span has `acme.scenario`)."""
    for t in traces:
        if t.trace_id not in exclude and any(
            s.attrs.get("acme.scenario") == scenario_id for s in t.spans
        ):
            return t
    return None


def span_kinds_seen(traces: list[Trace], limit: int = 25) -> list[str]:
    """Distinct span names in the window (bounded), to tell a product gap from harness timing."""
    return sorted({s.name for t in traces for s in t.spans})[:limit]


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
    settle_wait: float = 120.0,
    poll: float = 15.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
) -> Report:
    """Run presets, claim each turn's Customer step trace, then settle and assert on the whole run.

    Per turn: poll a window starting at that turn (cheap, keeps attribution). A turn whose trace
    is not there after `ingest_wait` becomes *pending*, not a failure. Every listed trace is kept
    (deduplicated by trace_id, richer copies win), so nothing is lost at a turn boundary.
    After all presets, a settle pass lists the whole run window (`run_start` .. now), polling with
    widening intervals for up to `settle_wait` only while turns are pending or span kinds are
    missing. Assertions use the settled set. List calls: ~1-2 per turn plus <= 6 in the settle pass.
    """
    report = Report(project=project)
    pii = fixture_pii() if pii is None else pii
    pool: dict[str, Trace] = {}
    claimed: set[str] = set()
    pending: list[tuple[str, int]] = []
    order = {sid: i for i, sid in enumerate(presets)}
    run_start = now() - timedelta(seconds=5)

    def absorb(window: list[Trace]) -> None:
        for t in window:
            if t.trace_id not in pool or len(t.spans) > len(pool[t.trace_id].spans):
                pool[t.trace_id] = t

    def claim(sid: str, turn: int) -> bool:
        found = step_trace(list(pool.values()), sid, claimed)
        if found is None:
            return False
        claimed.add(found.trace_id)
        report.rows.append(Row(sid, turn, found.trace_id))
        return True

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
            deadline, delay = monotonic() + ingest_wait, poll
            while True:  # Cloud Trace ingestion lags; poll until the step trace appears
                absorb(await traces.list_traces(start, now() + timedelta(seconds=5)))
                if claim(sid, turn) or monotonic() >= deadline:
                    break
                await sleep(delay)
                delay = min(delay * POLL_GROWTH, POLL_MAX)  # fewer list calls as the wait grows
            if not any(r.scenario == sid and r.turn == turn for r in report.rows):
                pending.append((sid, turn))  # settled below, not failed yet

    # Settle pass: one run-wide window, re-listed with backoff only while something is missing.
    deadline, delay = monotonic() + settle_wait, poll
    while True:
        absorb(await traces.list_traces(run_start, now() + timedelta(seconds=5)))
        pending = [(sid, turn) for sid, turn in pending if not claim(sid, turn)]
        if (not pending and not missing_spans(list(pool.values()))) or monotonic() >= deadline:
            break
        await sleep(delay)
        delay = min(delay * POLL_GROWTH, POLL_MAX)

    settled = list(pool.values())
    pages = getattr(traces, "pages_fetched", None)
    capped = "yes" if getattr(traces, "cap_hit", False) else "no"
    paging = "" if pages is None else f"; {pages} pages fetched, page cap hit: {capped}"
    scanned = f"{len(settled)} traces scanned in the run window{paging}; span names seen: " \
              f"{span_kinds_seen(settled)}"  # fmt: skip
    report.failures += [f"{sid} turn {turn}: no Customer step trace in Cloud Trace after settle "
                        f"({scanned})" for sid, turn in pending]  # fmt: skip
    report.failures += [f"missing span kind {m!r} after settle ({scanned})"
                        for m in missing_spans(settled)]  # fmt: skip
    report.failures += pii_failures(settled, pii)
    report.rows.sort(key=lambda r: (order[r.scenario], r.turn))
    return report


class TraceListTruncated(RuntimeError):
    """The listing hit the page cap while more pages remained: never silently truncated."""


class TraceQueryFailed(RuntimeError):
    """Cloud Trace kept failing transiently (429/5xx/network) until the retry budget ran out."""


class CloudTraceV1:
    """Cloud Trace v1 REST (`projects.traces.list`, COMPLETE view) with ADC. Never used in CI.

    Transient failures (HTTP 429/500/502/503/504 and network errors) are retried with exponential
    backoff and full jitter, honouring `Retry-After`, within `max_attempts` and `max_total`
    seconds of sleeping; then `TraceQueryFailed`. Any other 4xx (e.g. 403) fails immediately.
    """

    def __init__(
        self,
        project: str,
        *,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        max_attempts: int = 6,
        max_total: float = 90.0,
        base: float = 2.0,
        cap: float = 30.0,
        span_prefix: str = SPAN_FILTER_PREFIX,
        max_pages: int = MAX_PAGES,
    ) -> None:
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/trace.readonly"]
            )
            session = AuthorizedSession(creds)
        self._session = session
        self.project = project
        self._sleep, self._jitter = sleep, jitter
        self._span_prefix, self._max_pages = span_prefix, max_pages
        self.pages_fetched, self.cap_hit = 0, False  # cumulative, for failure messages
        self._max_attempts, self._max_total, self._base, self._cap = (
            max_attempts, max_total, base, cap,
        )  # fmt: skip

    async def list_traces(self, start: datetime, end: datetime) -> list[Trace]:
        return await asyncio.to_thread(self._list, start, end)

    def _delay(self, attempt: int, response: Any) -> float:
        retry_after = getattr(response, "headers", {}).get("Retry-After") if response else None
        try:
            if retry_after is not None:
                return min(max(float(retry_after), 0.0), self._cap)
        except ValueError:
            pass  # an HTTP-date Retry-After: fall back to backoff
        ceiling = min(self._cap, self._base * 2**attempt)
        return ceiling * (0.5 + self._jitter() / 2)  # jitter in [50%, 100%] of the ceiling

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        slept = 0.0
        for attempt in range(self._max_attempts):
            response, error = None, ""
            try:
                response = self._session.get(url, params=params, timeout=30)
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()  # non-transient 4xx raises here, no retry
                    return response
                error = f"HTTP {response.status_code}"
            except OSError as exc:
                if response is not None:  # raise_for_status() on a non-retryable status
                    raise
                error = f"{type(exc).__name__}: {exc}"
            delay = self._delay(attempt, response)
            if attempt + 1 >= self._max_attempts or slept + delay > self._max_total:
                raise TraceQueryFailed(
                    f"Cloud Trace list failed after {attempt + 1} attempt(s), "
                    f"{slept:.0f}s of backoff; last error: {error}"
                )
            self._sleep(delay)
            slept += delay
        raise AssertionError("unreachable")  # pragma: no cover

    def _list(self, start: datetime, end: datetime) -> list[Trace]:
        url = f"https://cloudtrace.googleapis.com/v1/projects/{self.project}/traces"
        params: dict[str, Any] = {"view": "COMPLETE", "pageSize": PAGE_SIZE,
                                  "startTime": start.isoformat().replace("+00:00", "Z"),
                                  "endTime": end.isoformat().replace("+00:00", "Z")}  # fmt: skip
        if self._span_prefix:  # server-side: unrelated hub/demo traffic cannot crowd out ours
            params["filter"] = f"span:{self._span_prefix}"
        out: list[Trace] = []
        for _page in range(self._max_pages):
            body = self._get(url, params).json()
            self.pages_fetched += 1
            for t in body.get("traces", []):
                spans = tuple(Span(s["name"], dict(s.get("labels", {}))) for s in t["spans"])
                out.append(Trace(t["traceId"], spans))
            if not body.get("nextPageToken"):
                return out
            params["pageToken"] = body["nextPageToken"]
        self.cap_hit = True
        raise TraceListTruncated(
            f"Cloud Trace listing still had more pages after {self._max_pages} pages "
            f"({len(out)} traces) for the window {params['startTime']}..{params['endTime']}; "
            "narrow the window or the filter instead of reading a truncated set"
        )
