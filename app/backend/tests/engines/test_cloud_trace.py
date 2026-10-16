"""configure_cloud_trace (#163): fake exporter, no GCP."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter

from acme import telemetry


class FakeExporter(SpanExporter):
    def __init__(self, project_id=None, resource_regex=None):
        self.project_id = project_id
        self.resource_regex = resource_regex

    def export(self, spans):
        return 0

    def shutdown(self):
        pass


class Recorder:
    def __init__(self, monkeypatch, current):
        self.set = []
        monkeypatch.setattr(telemetry.trace, "get_tracer_provider", lambda: current)
        monkeypatch.setattr(telemetry.trace, "set_tracer_provider", self.set.append)


def test_env_unset_changes_nothing(monkeypatch):
    monkeypatch.delenv(telemetry.TELEMETRY_ENV, raising=False)
    rec = Recorder(monkeypatch, object())
    assert telemetry.configure_cloud_trace("acme-customer-agent", FakeExporter) is False
    assert rec.set == []


def test_env_set_with_default_provider_installs_one_named_after_the_engine(monkeypatch):
    monkeypatch.setenv(telemetry.TELEMETRY_ENV, "true")
    rec = Recorder(monkeypatch, object())  # not an SDK provider (proxy/no-op)
    assert telemetry.configure_cloud_trace("acme-sales-agent", FakeExporter) is True
    (provider,) = rec.set
    assert provider.resource.attributes["service.name"] == "acme-sales-agent"


def test_existing_sdk_provider_is_left_untouched(monkeypatch):
    monkeypatch.setenv(telemetry.TELEMETRY_ENV, "true")
    rec = Recorder(monkeypatch, TracerProvider())
    assert telemetry.configure_cloud_trace("acme-sales-agent", FakeExporter) is False
    assert rec.set == []


def test_a_failing_exporter_never_raises(monkeypatch):
    monkeypatch.setenv(telemetry.TELEMETRY_ENV, "true")
    Recorder(monkeypatch, object())

    def boom(project_id=None, resource_regex=None):
        raise RuntimeError("no ADC")

    assert telemetry.configure_cloud_trace("acme-sales-agent", boom) is False


def test_demo_entrypoints_do_not_import_the_otel_sdk_or_exporter():
    import subprocess
    import sys

    code = (
        "import sys, acme.demo.supervisor, acme.api.app, acme.sales.main, acme.customer.main;"
        "bad=[k for k in sys.modules if k.startswith('opentelemetry.sdk') or"
        " k.startswith('opentelemetry.exporter')]; assert not bad, bad"
    )
    subprocess.run([sys.executable, "-W", "ignore", "-c", code], check=True)


def test_acme_enable_tracing_flag_installs_the_exporter_without_a_google_cloud_name(monkeypatch):
    monkeypatch.delenv(telemetry.TELEMETRY_ENV, raising=False)
    monkeypatch.setenv("ACME_ENABLE_TRACING", "true")
    rec = Recorder(monkeypatch, object())
    assert telemetry.configure_cloud_trace("acme-customer-agent", FakeExporter) is True
    assert len(rec.set) == 1


def _project_id_passed(monkeypatch, env):
    for k in ("ACME_GENAI_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ACME_ENABLE_TRACING", "true")
    Recorder(monkeypatch, object())
    seen = []

    def factory(**kw):
        seen.append(kw)
        return FakeExporter(**kw)

    assert telemetry.configure_cloud_trace("acme-customer-agent", factory) is True
    return seen


def test_injected_project_number_never_reaches_the_exporter(monkeypatch):
    """#203: Agent Engine injects GOOGLE_CLOUD_PROJECT=<number>; Cloud Trace needs the ID."""
    seen = _project_id_passed(
        monkeypatch,
        {"GOOGLE_CLOUD_PROJECT": "493084803309", "ACME_GENAI_PROJECT": "acme-airlines-factory"},
    )
    assert seen == [
        {"project_id": "acme-airlines-factory", "resource_regex": telemetry.RESOURCE_REGEX}
    ]


def test_project_id_falls_back_to_the_llm_config_project(monkeypatch):
    seen = _project_id_passed(monkeypatch, {"GOOGLE_CLOUD_PROJECT": "493084803309"})
    assert seen == [
        {"project_id": "acme-airlines-factory", "resource_regex": telemetry.RESOURCE_REGEX}
    ]


def test_service_name_reaches_the_cloud_trace_span_and_nothing_else_of_the_resource():
    """#400: with the regex the exporter keeps `service.name`; without it, the label is lost."""
    from unittest.mock import MagicMock

    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    def translated(regex, service):
        memory = InMemorySpanExporter()
        prov = TracerProvider(
            resource=Resource.create({"service.name": service, "host.name": "secret-host"})
        )
        prov.add_span_processor(SimpleSpanProcessor(memory))
        with prov.get_tracer("t").start_as_current_span("acme.engine.op"):
            pass
        exporter = CloudTraceSpanExporter(
            project_id="acme-airlines-factory", client=MagicMock(), resource_regex=regex
        )
        (span,) = exporter._translate_to_cloud_trace(memory.get_finished_spans())
        return {k: v.string_value.value for k, v in span.attributes.attribute_map.items()}

    for service in ("acme-customer-agent", "acme-sales-agent"):
        attrs = translated(telemetry.RESOURCE_REGEX, service)
        assert attrs["service.name"] == service
        assert "host.name" not in attrs
    assert "service.name" not in translated(None, "acme-customer-agent")  # the bug
