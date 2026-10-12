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
