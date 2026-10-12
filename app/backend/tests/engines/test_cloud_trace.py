"""configure_cloud_trace (#163): fake exporter, no GCP."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter

from acme import telemetry


class FakeExporter(SpanExporter):
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

    def boom():
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
