"""Suite-wide telemetry setup: strict allowlist and one in-memory span exporter."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from acme import telemetry

_EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_provider)


@pytest.fixture(autouse=True)
def _strict_telemetry():
    telemetry.set_strict(True)
    yield
    telemetry.set_strict(True)


@pytest.fixture
def spans():
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


# One in-memory metric reader for the whole suite (a global MeterProvider can be set only once).
from opentelemetry import metrics as _otel_metrics  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402

_READER = InMemoryMetricReader()
_otel_metrics.set_meter_provider(MeterProvider(metric_readers=[_READER]))


@pytest.fixture
def metric_points():
    """`points()` -> {metric name: [(attributes, value-or-count), ...]} since the last call."""

    def points() -> dict[str, list[tuple[dict, float]]]:
        out: dict[str, list[tuple[dict, float]]] = {}
        data = _READER.get_metrics_data()
        for rm in data.resource_metrics if data else []:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    for p in m.data.data_points:
                        value = getattr(p, "value", None)
                        value = p.count if value is None else value
                        out.setdefault(m.name, []).append((dict(p.attributes), value))
        return out

    return points
