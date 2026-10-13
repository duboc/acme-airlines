"""#221 instrumentation: every engine operation logs one structured line that lets a missing
Cloud Trace trace be matched to an exporter outcome (trace id, recording/sampled flags, flush
result, pid, instance), and exporter failures are surfaced, not swallowed."""

import json
import logging
import re

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from acme import telemetry
from acme.engines.customer import build_customer_engine_app
from acme.engines.sales import build_sales_engine_app
from tests.governance.flow import Flow, engine_over


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        msg = record.getMessage()
        assert msg.startswith("ACME_DIAG "), msg
        self.records.append(json.loads(msg[len("ACME_DIAG ") :]))


@pytest.fixture
def diag_lines():
    handler = Lines()
    logger = logging.getLogger("acme.diag")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler.records
    logger.removeHandler(handler)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ACME_STATE_DB", raising=False)
    exp = InMemorySpanExporter()
    prov = TracerProvider()
    prov.add_span_processor(BatchSpanProcessor(exp, schedule_delay_millis=3_600_000))
    monkeypatch.setattr(telemetry, "tracer", prov.get_tracer("acme"))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: prov)
    yield prov
    prov.shutdown()


def ops(lines, op=None):
    return [r for r in lines if r["event"] == "op" and (op is None or r["op"] == op)]


async def test_every_customer_operation_logs_one_structured_line(provider, diag_lines):
    app = build_customer_engine_app()
    app.set_up()
    app.scenario_start("happy-path")
    await app.scenario_step()
    app.journeys_list()
    got = {r["op"] for r in ops(diag_lines)}
    assert {"scenario_start", "scenario_step", "journeys_list"} <= got
    (line,) = ops(diag_lines, "journeys_list")
    assert re.fullmatch(r"[0-9a-f]{32}", line["trace_id"])
    assert re.fullmatch(r"[0-9a-f]{16}", line["span_id"])
    assert line["is_recording"] is True and line["sampled"] is True
    assert line["flush_ok"] is True and isinstance(line["flush_ms"], (int, float))
    assert (
        isinstance(line["pid"], int) and line["instance"] and line["provider"] == "TracerProvider"
    )
    assert line["parent"] == {"valid": False, "sampled": False}  # no incoming context here


async def test_an_unsampled_incoming_context_is_visible_in_the_log(provider, diag_lines):
    """A platform root that is not sampled makes our spans non-recording: the log shows it."""
    app = build_customer_engine_app()
    app.set_up()
    ctx = SpanContext(0xABC, 0xDEF, True, TraceFlags(TraceFlags.DEFAULT))
    with trace.use_span(NonRecordingSpan(ctx)):
        app.journeys_list()
    (line,) = ops(diag_lines, "journeys_list")
    assert line["parent"] == {"valid": True, "sampled": False}
    assert line["is_recording"] is False and line["sampled"] is False


def test_log_lines_hold_only_ids_flags_and_counters(provider, diag_lines):
    app = build_customer_engine_app()
    app.set_up()
    app.journeys_list()
    (line,) = ops(diag_lines)
    assert set(line) == {
        "event", "ts", "op", "trace_id", "span_id", "is_recording", "sampled", "parent",
        "flush_ok", "flush_ms", "flush_error", "pid", "instance", "provider", "proc", "container",
        "scenario", "step", "shared_version", "loaded_version", "db_inode",
    }  # fmt: skip


async def test_sales_tick_logs_its_line(provider, diag_lines):
    f = Flow("engine")
    await f.cancel()
    app = build_sales_engine_app(engine_over(f.rt))
    app.set_up()
    [e async for e in app.async_stream_query(message="tick", user_id="u")]
    assert ops(diag_lines, "sales_tick")


def test_flush_reports_failures_instead_of_hiding_them(monkeypatch):
    class Bad:
        def force_flush(self, timeout):
            raise RuntimeError("boom")

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: Bad())
    res = telemetry.flush()
    assert res.ok is False and res.error == "RuntimeError"

    class Slow:
        def force_flush(self, timeout):
            return False  # timed out

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: Slow())
    assert telemetry.flush().ok is False
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: object())
    assert telemetry.flush().ok is None  # no SDK provider


def test_exporter_failures_are_logged_as_structured_lines(monkeypatch, diag_lines):
    class Failing(SpanExporter):
        def export(self, spans):
            return SpanExportResult.FAILURE

        def shutdown(self):
            pass

    class Raising(Failing):
        def export(self, spans):
            raise ConnectionError("429 batchWrite")

    class Ok(Failing):
        def export(self, spans):
            return SpanExportResult.SUCCESS

    class Span:  # only len() is used
        pass

    for inner, ok, error in ((Failing(), False, None), (Raising(), False, "ConnectionError"),
                             (Ok(), True, None)):  # fmt: skip
        diag_lines.clear()
        wrapped = telemetry.logged_exporter(inner)
        result = wrapped.export([Span(), Span()])
        (line,) = [r for r in diag_lines if r["event"] == "export"]
        assert line["ok"] is ok and line["n"] == 2 and line.get("error") == error
        assert (result is SpanExportResult.SUCCESS) is ok
