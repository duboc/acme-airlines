"""#221 root cause: Agent Engine head-samples incoming requests (roughly one per 10-20 s) and our
provider was ParentBased, so an unsampled platform parent silenced every span of the request,
including the platform's own server spans. The engine provider must record regardless."""

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from acme import telemetry
from acme.engines.customer import build_customer_engine_app


def unsampled_parent():
    ctx = SpanContext(0xABCDEF, 0x123456, True, TraceFlags(TraceFlags.DEFAULT))
    return trace.use_span(NonRecordingSpan(ctx))


def provider_with(monkeypatch, env=None):
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    exp = InMemorySpanExporter()
    prov = telemetry.build_provider("acme-customer-agent", exp)
    return prov, exp


def test_a_span_under_an_unsampled_platform_parent_is_recorded_and_exported(monkeypatch):
    monkeypatch.delenv("ACME_TRACE_SAMPLER", raising=False)
    prov, exp = provider_with(monkeypatch)
    tracer = prov.get_tracer("t")
    with unsampled_parent():
        with tracer.start_as_current_span("POST /api/reasoning_engine") as root:
            assert root.is_recording()
            with tracer.start_as_current_span("acme.engine.op"):
                pass
    prov.force_flush()
    assert {s.name for s in exp.get_finished_spans()} == {
        "POST /api/reasoning_engine",
        "acme.engine.op",
    }
    assert all(s.context.trace_id == 0xABCDEF for s in exp.get_finished_spans())  # same trace id


def test_the_parent_sampler_can_be_selected_by_env(monkeypatch):
    prov, exp = provider_with(monkeypatch, {"ACME_TRACE_SAMPLER": "parent"})
    assert isinstance(prov.sampler, ParentBased)
    with unsampled_parent(), prov.get_tracer("t").start_as_current_span("x") as s:
        assert not s.is_recording()  # the old behaviour, opt-in only


def test_an_unknown_sampler_value_falls_back_to_always_on(monkeypatch):
    prov, _ = provider_with(monkeypatch, {"ACME_TRACE_SAMPLER": "banana"})
    with unsampled_parent(), prov.get_tracer("t").start_as_current_span("x") as s:
        assert s.is_recording()


def test_configure_cloud_trace_installs_the_always_on_provider(monkeypatch):
    installed = []
    monkeypatch.setenv("ACME_ENABLE_TRACING", "true")
    monkeypatch.delenv("ACME_TRACE_SAMPLER", raising=False)
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: object())
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)

    class Exporter(InMemorySpanExporter):
        def __init__(self, project_id=None, resource_regex=None):
            super().__init__()
            assert resource_regex == telemetry.RESOURCE_REGEX

    assert telemetry.configure_cloud_trace("acme-customer-agent", Exporter) is True
    (prov,) = installed
    with unsampled_parent(), prov.get_tracer("t").start_as_current_span("x") as s:
        assert s.is_recording()  # what was lost on the deployed engines


async def test_engine_operations_under_an_unsampled_parent_are_recorded_and_exported(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ACME_TRACE_SAMPLER", raising=False)
    prov, exp = provider_with(monkeypatch)
    monkeypatch.setattr(telemetry, "tracer", prov.get_tracer("acme"))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: prov)
    app = build_customer_engine_app()
    app.set_up()
    app.scenario_start("happy-path")
    exp.clear()
    with unsampled_parent():  # the platform did not sample this request (step 0 / a2a / poll)
        await app.scenario_step()
    names = {s.name for s in exp.get_finished_spans()}
    assert {"acme.engine.op", "acme.scenario.step"} <= names
    step = next(s for s in exp.get_finished_spans() if s.name == "acme.scenario.step")
    assert step.attributes["acme.scenario"] == "happy-path"
