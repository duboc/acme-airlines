"""Logic of the live Agent Engine trace proof (#167). No GCP imports at module level.

`run_proof` drives presets through an `EngineBackend`-shaped object, finds each turn's Cloud
Trace traces through an injectable `TraceSource`, and checks spans and PII. The engine and the
trace layers are injected so CI unit-tests this with fakes (`test_agent_engine_proof_logic.py`).
"""

from __future__ import annotations

import asyncio
import json
import random
import re
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
    span_id: str = ""
    parent_id: str = ""
    start: str = ""
    end: str = ""


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
    inventory: dict[str, Any] | None = None  # trace-inventory diagnostic (see `build_inventory`)

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


def scrub(text: str, pii: set[str]) -> str:
    return "[redacted]" if any(v in text for v in pii) else text


def inventory_trace(t: Trace, pii: set[str]) -> dict[str, Any]:
    """One trace for the diagnostic dump: span names/times/parents, attribute KEYS for every span,
    and values only for `acme.*` attributes, scrubbed with the proof's PII list."""
    return {"trace_id": t.trace_id, "spans": [
        {"name": scrub(sp.name, pii), "span_id": sp.span_id, "parent_id": sp.parent_id,
         "start": sp.start, "end": sp.end, "attr_keys": sorted(sp.attrs),
         "acme": {k: scrub(v, pii) for k, v in sorted(sp.attrs.items()) if k.startswith("acme.")}}
        for sp in t.spans
    ]}  # fmt: skip


def build_inventory(
    turns: list[dict[str, Any]],
    filtered: list[Trace],
    unfiltered: list[Trace] | None,
    listings: list[dict[str, Any]],
    pii: set[str],
) -> dict[str, Any]:
    """JSON-able inventory of what the harness expected and what Cloud Trace actually returned."""
    return {
        "turns": turns,
        "listings": listings,
        "filtered_traces": [inventory_trace(t, pii) for t in filtered],
        "unfiltered_traces": (
            None if unfiltered is None else [inventory_trace(t, pii) for t in unfiltered]
        ),
    }


def write_inventory(report: Report, path: str) -> None:
    from pathlib import Path

    Path(path).write_text(json.dumps(report.inventory, indent=1, sort_keys=True))


CUSTOMER_UNARY = "POST /api/reasoning_engine"  # Sales is `stream_reasoning_engine`
STEP_SPAN = "acme.scenario.step"


_TS = re.compile(r"^(?P<head>[^.Z+]+)(?:\.(?P<frac>\d+))?(?P<tz>Z|[+-]\d\d:\d\d)?$")


def _parse_ts(text: str) -> datetime | None:
    """RFC 3339 (with optional nanoseconds, `Z` or an offset) -> aware datetime (microseconds)."""
    m = _TS.match(text)
    if not m:
        return None
    frac = (m["frac"] or "0")[:6].ljust(6, "0")
    tz = "+00:00" if m["tz"] in (None, "Z") else m["tz"]
    try:
        return datetime.fromisoformat(f"{m['head']}.{frac}{tz}")
    except ValueError:
        return None


def diagnose_turn(unfiltered: list[Trace] | None, turn: dict[str, Any]) -> str:
    """Why is this turn unmatched: `absent` (no Customer unary trace of any kind started during
    the turn, in the UNFILTERED run listing) vs `partial` (one did, but without the step span)."""
    if unfiltered is None:
        return "diagnosis: unknown (no unfiltered listing)"
    lo, hi = _parse_ts(turn["sent_at"]), _parse_ts(turn["received_at"])
    started = []
    for t in unfiltered:
        root = next((sp for sp in t.spans if sp.name == CUSTOMER_UNARY), None)
        at = _parse_ts(root.start) if root else None
        if root and at and lo and hi and lo <= at <= hi:
            started.append(t)
    if not started:
        return (
            f"diagnosis: ABSENT, no Customer {CUSTOMER_UNARY!r} trace of any kind started during "
            f"the turn ({len(unfiltered)} traces in the unfiltered run window)"
        )
    counts = [len(t.spans) for t in started]
    if any(sp.name == STEP_SPAN for t in started for sp in t.spans):
        return "diagnosis: step span present in the unfiltered listing but not matched (harness)"
    return (f"diagnosis: PARTIAL, {len(started)} Customer trace(s) started during the turn but "
            f"none has an {STEP_SPAN!r} span (span counts {counts})")  # fmt: skip


def customer_call_summary(turn_log: list[dict[str, Any]]) -> dict[str, int]:
    matched = sum(1 for t in turn_log if t.get("matched_trace_id"))
    return {"steps_expected": len(turn_log), "steps_with_trace": matched,
            "steps_missing": len(turn_log) - matched}  # fmt: skip


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
    dump: bool = False,
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
    turn_log: list[dict[str, Any]] = []

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
            sent_at = now()
            got = await backend.scenario_step(sid)
            turn_log.append({
                "scenario": sid, "turn": turn, "sent_at": sent_at.isoformat(),
                "received_at": now().isoformat(), "window_start": start.isoformat(),
                **{k: got[k] for k in ("session_id", "journey_id", "scenario_id") if k in got},
            })  # fmt: skip
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
    pii_hits = pii_failures(settled, pii)
    unfiltered: list[Trace] | None = None
    if dump or pending or report.failures or missing_spans(settled) or pii_hits:
        # Diagnostics: also list WITHOUT the filter, to tell an absent trace from a partial one.
        probe = getattr(traces, "list_unfiltered", None)
        if probe is not None:
            try:
                unfiltered = await probe(run_start, now() + timedelta(seconds=5))
            except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the failures
                report.failures.append(f"diagnostic unfiltered listing failed: {exc}")
    log_by_turn = {(t["scenario"], t["turn"]): t for t in turn_log}
    diagnosis = {
        key: diagnose_turn(unfiltered, log_by_turn[key]) for key in pending if key in log_by_turn
    }
    for key, text in diagnosis.items():
        log_by_turn[key]["diagnosis"] = text
    pages = getattr(traces, "pages_fetched", None)
    capped = "yes" if getattr(traces, "cap_hit", False) else "no"
    paging = "" if pages is None else f"; {pages} pages fetched, page cap hit: {capped}"
    scanned = f"{len(settled)} traces scanned in the run window{paging}; span names seen: " \
              f"{span_kinds_seen(settled)}"  # fmt: skip
    report.failures += [f"{sid} turn {turn}: no Customer step trace in Cloud Trace after settle "
                        f"({scanned}); {diagnosis.get((sid, turn), 'diagnosis unavailable')}"
                        for sid, turn in pending]  # fmt: skip
    report.failures += [f"missing span kind {m!r} after settle ({scanned})"
                        for m in missing_spans(settled)]  # fmt: skip
    report.failures += pii_hits
    report.rows.sort(key=lambda r: (order[r.scenario], r.turn))
    if dump or report.failures:
        for row in report.rows:
            if (row.scenario, row.turn) in log_by_turn:
                log_by_turn[(row.scenario, row.turn)]["matched_trace_id"] = row.trace_id
        report.inventory = build_inventory(
            turn_log, settled, unfiltered, list(getattr(traces, "listings", [])), pii
        )
        report.inventory["customer_calls"] = customer_call_summary(turn_log)
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
        self.listings: list[dict[str, Any]] = []  # one record per listing call (diagnostics)
        self._max_attempts, self._max_total, self._base, self._cap = (
            max_attempts, max_total, base, cap,
        )  # fmt: skip

    async def list_traces(self, start: datetime, end: datetime) -> list[Trace]:
        return await asyncio.to_thread(self._list, start, end)

    async def list_unfiltered(self, start: datetime, end: datetime) -> list[Trace]:
        """Diagnostics only: the same window without the server-side filter (may hit the cap)."""
        return await asyncio.to_thread(self._list, start, end, "")

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

    def _list(self, start: datetime, end: datetime, span_prefix: str | None = None) -> list[Trace]:
        prefix = self._span_prefix if span_prefix is None else span_prefix
        url = f"https://cloudtrace.googleapis.com/v1/projects/{self.project}/traces"
        params: dict[str, Any] = {"view": "COMPLETE", "pageSize": PAGE_SIZE,
                                  "startTime": start.isoformat().replace("+00:00", "Z"),
                                  "endTime": end.isoformat().replace("+00:00", "Z")}  # fmt: skip
        if prefix:  # server-side: unrelated hub/demo traffic cannot crowd out ours
            params["filter"] = f"span:{prefix}"
        record = {"start": params["startTime"], "end": params["endTime"],
                  "filter": params.get("filter", ""), "pages": 0, "traces": 0,
                  "cap_hit": False}  # fmt: skip
        self.listings.append(record)
        out: list[Trace] = []
        for _page in range(self._max_pages):
            body = self._get(url, params).json()
            self.pages_fetched += 1
            record["pages"] += 1
            for t in body.get("traces", []):
                spans = tuple(
                    Span(
                        s["name"], dict(s.get("labels", {})), s.get("spanId", ""),
                        s.get("parentSpanId", ""), s.get("startTime", ""), s.get("endTime", ""),
                    )
                    for s in t["spans"]
                )  # fmt: skip
                out.append(Trace(t["traceId"], spans))
            record["traces"] = len(out)
            if not body.get("nextPageToken"):
                return out
            params["pageToken"] = body["nextPageToken"]
        self.cap_hit = record["cap_hit"] = True
        raise TraceListTruncated(
            f"Cloud Trace listing still had more pages after {self._max_pages} pages "
            f"({len(out)} traces) for the window {params['startTime']}..{params['endTime']}; "
            "narrow the window or the filter instead of reading a truncated set"
        )


# --- #221: engine-log attachment -------------------------------------------
class CloudLogging:
    """Cloud Logging `entries:list` for one engine's log lines (the deployer has logging.viewer).
    A read failure is reported by `fetch_or_error`, never fatal to the proof."""

    def __init__(self, project: str, *, session: Any = None, max_pages: int = 5) -> None:
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/logging.read"])
            session = AuthorizedSession(creds)
        self._session, self.project, self._max_pages = session, project, max_pages

    def fetch(self, engine: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        engine_id = engine.rstrip("/").rsplit("/", 1)[-1]
        stamp = lambda d: d.astimezone(UTC).isoformat().replace("+00:00", "Z")  # noqa: E731
        body: dict[str, Any] = {
            "resourceNames": [f"projects/{self.project}"],
            "filter": (
                'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
                f'AND resource.labels.reasoning_engine_id="{engine_id}" '
                f'AND timestamp>="{stamp(start)}" AND timestamp<="{stamp(end)}"'
            ),
            "orderBy": "timestamp asc",
            "pageSize": 1000,
        }
        out: list[dict[str, Any]] = []
        for _ in range(self._max_pages):
            resp = self._session.post(
                "https://logging.googleapis.com/v2/entries:list", json=body, timeout=60
            )
            resp.raise_for_status()
            data = resp.json()
            for e in data.get("entries", []):
                text = e.get("textPayload") or json.dumps(e.get("jsonPayload", {}), sort_keys=True)
                out.append({"ts": e.get("timestamp", ""), "severity": e.get("severity", ""),
                            "text": text})  # fmt: skip
            if not data.get("nextPageToken"):
                break
            body["pageToken"] = data["nextPageToken"]
        return out

    def fetch_or_error(self, engine: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        try:
            return self.fetch(engine, start, end)
        except Exception as exc:  # noqa: BLE001 - diagnostics must not fail the proof
            return [{"_error": type(exc).__name__}]  # the type name only, never the message


# The engine-logs artifact is downloadable by anyone (public repository), so it is ALLOWLIST-only:
# only `ACME_DIAG` lines (acme.telemetry.diag: ids, flags and counters) are parsed, and only the
# fields below survive, each type- and pattern-checked so no free text can pass through a
# permitted key. Every other log line (tracebacks, container stderr, request payloads, anything
# with names or tokens) is dropped and only counted.
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,59}$")
_HOST = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_HEX32, _HEX16 = re.compile(r"^[0-9a-f]{32}$"), re.compile(r"^[0-9a-f]{16}$")
_LOG_TS = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+(Z|[+-]\d\d:\d\d)$")
_SEVERITIES = frozenset({"DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT",
                         "EMERGENCY", "DEFAULT"})  # fmt: skip
_ERRORS = frozenset({"ERROR", "CRITICAL", "ALERT", "EMERGENCY"})


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and abs(v) < 10**12


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 10**12


def _matches(rx: re.Pattern[str]) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and bool(rx.match(v))


DIAG_FIELDS: dict[str, dict[str, Callable[[Any], bool]]] = {
    "op": {
        "op": _matches(_NAME), "trace_id": _matches(_HEX32), "span_id": _matches(_HEX16),
        "is_recording": _is_bool, "sampled": _is_bool, "flush_ok": _is_bool, "flush_ms": _is_num,
        "flush_error": _matches(_NAME), "pid": _is_int, "instance": _matches(_HOST),
        "provider": _matches(_NAME),
    },
    "export": {"ok": _is_bool, "n": _is_int, "ms": _is_num, "error": _matches(_NAME)},
}  # fmt: skip


def allowlisted_diag(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Allowlisted fields of one log entry, or None unless it is a well-formed ACME_DIAG line."""
    text = entry.get("text", "")
    if not isinstance(text, str) or not text.startswith("ACME_DIAG "):
        return None
    try:
        data = json.loads(text[len("ACME_DIAG ") :])
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("event") not in DIAG_FIELDS:
        return None
    out: dict[str, Any] = {"event": data["event"]}
    if entry.get("severity") in _SEVERITIES:
        out["severity"] = entry["severity"]
    if _matches(_LOG_TS)(entry.get("ts")):
        out["ts"] = entry["ts"]
    for key, ok in DIAG_FIELDS[data["event"]].items():
        if key in data and ok(data[key]):
            out[key] = data[key]
    parent = data.get("parent")
    if data["event"] == "op" and isinstance(parent, dict):
        for key in ("valid", "sampled"):
            if _is_bool(parent.get(key)):
                out[f"parent_{key}"] = parent[key]
    return out


def write_engine_logs(path: str, by_engine: dict[str, list[dict[str, Any]]]) -> None:
    """JSON lines of allowlisted ACME_DIAG fields per engine, plus per-engine counts of the lines
    that were dropped (never their content) and a marker if the log read failed."""
    from pathlib import Path

    rows: list[dict[str, Any]] = []
    for name, entries in by_engine.items():
        dropped = errors = 0
        for e in entries:
            if "_error" in e:
                err = e["_error"] if _matches(_NAME)(e["_error"]) else "Error"
                rows.append({"engine": name, "event": "log_read_failed", "error": err})
            elif (kept := allowlisted_diag(e)) is not None:
                rows.append({"engine": name, **kept})
            else:
                dropped += 1
                errors += e.get("severity") in _ERRORS
        if dropped:
            rows.append({"engine": name, "event": "dropped", "lines": dropped, "errors": errors})
    Path(path).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
