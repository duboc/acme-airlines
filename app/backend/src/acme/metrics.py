"""OpenTelemetry product metrics with the same PII-free rules as `telemetry.span` (#235).

Instruments come from the global MeterProvider (a no-op until one is installed), so recording is
always safe. Attribute values are ids/enums only (`telemetry.is_safe_value`); a bad value is
replaced by `unknown`, never raised, so metrics can never break a journey. Metric names are listed
in `docs/observatory/product-metrics.md`.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import metrics

from acme.replay import is_replaying
from acme.telemetry import is_safe_value

_meter = metrics.get_meter("acme")

_guardrail = _meter.create_counter(
    "acme.guardrail.decisions", description="Guardrail decisions by rule (reason code) and outcome"
)
_offers = _meter.create_counter("acme.offers", description="Offers proposed/accepted/declined")
_fallbacks = _meter.create_counter(
    "acme.llm.template_fallbacks", description="Passenger messages that fell back to the template"
)
_ack_vetoes = _meter.create_counter(
    "acme.llm.ack_vetoes", description="Model acknowledgements downgraded by the deterministic veto"
)
_needs_human = _meter.create_counter("acme.needs_human", description="needs_human escalations")
_notify_latency = _meter.create_histogram(
    "acme.notification.latency", unit="s", description="RESOLVED to notification_sent"
)
# A rebuild takes tenths of a second: the SDK's default boundaries (0, 5, 10, ...) would put every
# one of them in the first bucket, and Cloud Monitoring would report p50 2.5 s (#344).
REBUILD_BUCKETS_S = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 2.0, 5.0)
_rebuild_duration = _meter.create_histogram(
    "acme.scenario.rebuild.duration",
    unit="s",
    description="Scenario rebuild by replay (#266)",
    explicit_bucket_boundaries_advisory=list(REBUILD_BUCKETS_S),
)
_http_5xx = _meter.create_counter("acme.http.server.errors", description="HTTP 5xx responses")


def _v(value: object) -> str:
    text = str(value)
    return text if is_safe_value(text) else "unknown"


def guardrail_decision(approved: bool, reasons: list[str], step: str) -> None:
    if is_replaying():  # a scenario rebuild must not double count (#266)
        return
    outcome = "approve" if approved else "reject"
    for rule in reasons if not approved else ["all"]:
        _guardrail.add(1, {"rule": _v(rule), "outcome": outcome, "step": _v(step)})


def offer(status: str) -> None:
    if is_replaying():  # a scenario rebuild must not double count (#266)
        return
    _offers.add(1, {"status": _v(status)})


def template_fallback(surface: str) -> None:
    if is_replaying():  # a scenario rebuild must not double count (#266)
        return
    _fallbacks.add(1, {"surface": _v(surface)})


def ack_veto() -> None:
    """A model Acknowledge the deterministic veto turned into Other (#421). No attributes: the
    passenger's text must never reach telemetry."""
    if is_replaying():  # a scenario rebuild must not double count (#266)
        return
    _ack_vetoes.add(1)


def needs_human(payload: dict[str, object]) -> None:
    if is_replaying():  # a scenario rebuild must not double count (#266)
        return
    who = payload.get("specialist") or str(payload.get("step", "unknown")).split(":")[0]
    _needs_human.add(1, {"specialist": _v(who)})


def notification_latency(seconds: float) -> None:
    if is_replaying():  # a scenario rebuild must not double count (#266)
        return
    _notify_latency.record(max(seconds, 0.0))


def rebuild_duration(seconds: float, preset: str, reason: str) -> None:
    """Not suppressed during replay: the rebuild is the very thing that is measured (#280). Call
    it outside the `replaying()` block, which only silences what the replay re-emits."""
    _rebuild_duration.record(max(seconds, 0.0), {"preset": _v(preset), "reason": _v(reason)})


def http_5xx(status_code: int) -> None:
    _http_5xx.add(1, {"status_code": _v(status_code)})


def add_http_metrics(app) -> None:
    """Count every 5xx response (including unhandled exceptions) on a FastAPI app."""

    @app.middleware("http")
    async def _count_5xx(request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            http_5xx(500)
            raise
        if response.status_code >= 500:
            http_5xx(response.status_code)
        return response


def build_meter_provider(service_name: str, readers: list):
    """The provider: our `acme.*` metrics only. The SDK's own `otel.sdk.*` self-metrics are
    dropped: Cloud Monitoring rejects some (`InvalidArgument`, #337) and nobody reads them."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.view import DropAggregation, View
    from opentelemetry.sdk.resources import Resource

    return MeterProvider(
        metric_readers=readers,
        resource=Resource.create({"service.name": service_name}),
        views=[View(instrument_name="otel.sdk.*", aggregation=DropAggregation())],
    )


def seed_counters() -> None:
    """Export `acme.http.server.errors` as 0 from the start: a counter that never moved is
    otherwise absent, and the dashboard cannot tell "no errors" from "no data" (#344)."""
    _http_5xx.add(0, {"status_code": "500"})


def configure_cloud_monitoring(service_name: str, exporter_factory=None) -> bool:
    """Install a Cloud Monitoring metric reader when tracing/telemetry is enabled and no SDK
    provider exists yet (same switch as `configure_cloud_trace`). Never raises."""
    from acme import telemetry

    if not any(
        os.environ.get(v, "").lower() in ("true", "1")
        for v in (telemetry.TRACING_ENV, telemetry.TELEMETRY_ENV)
    ):
        return False
    try:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        if isinstance(metrics.get_meter_provider(), MeterProvider):
            return False
        if exporter_factory is None:
            from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter

            def exporter_factory(project_id):  # noqa: F811
                # Several worker processes of one container export the same series: without an
                # identifier per exporter Cloud Monitoring rejects the colliding writes (#337).
                return CloudMonitoringMetricsExporter(
                    project_id=project_id, add_unique_identifier=True
                )

        from acme.llm import config as llm_config

        project_id = os.environ.get("ACME_GENAI_PROJECT") or llm_config.PROJECT
        reader = PeriodicExportingMetricReader(
            exporter_factory(project_id=project_id), export_interval_millis=30000
        )
        metrics.set_meter_provider(build_meter_provider(service_name, [reader]))
        seed_counters()
    except Exception:  # noqa: BLE001 - metrics must never stop an engine from starting
        logging.getLogger(__name__).warning("Cloud Monitoring not configured", exc_info=True)
        return False
    return True
