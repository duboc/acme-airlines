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

from acme.telemetry import is_safe_value

_meter = metrics.get_meter("acme")

_guardrail = _meter.create_counter(
    "acme.guardrail.decisions", description="Guardrail decisions by rule (reason code) and outcome"
)
_offers = _meter.create_counter("acme.offers", description="Offers proposed/accepted/declined")
_fallbacks = _meter.create_counter(
    "acme.llm.template_fallbacks", description="Passenger messages that fell back to the template"
)
_needs_human = _meter.create_counter("acme.needs_human", description="needs_human escalations")
_notify_latency = _meter.create_histogram(
    "acme.notification.latency", unit="s", description="RESOLVED to notification_sent"
)
_http_5xx = _meter.create_counter("acme.http.server.errors", description="HTTP 5xx responses")


def _v(value: object) -> str:
    text = str(value)
    return text if is_safe_value(text) else "unknown"


def guardrail_decision(approved: bool, reasons: list[str], step: str) -> None:
    outcome = "approve" if approved else "reject"
    for rule in reasons if not approved else ["all"]:
        _guardrail.add(1, {"rule": _v(rule), "outcome": outcome, "step": _v(step)})


def offer(status: str) -> None:
    _offers.add(1, {"status": _v(status)})


def template_fallback(surface: str) -> None:
    _fallbacks.add(1, {"surface": _v(surface)})


def needs_human(payload: dict[str, object]) -> None:
    who = payload.get("specialist") or str(payload.get("step", "unknown")).split(":")[0]
    _needs_human.add(1, {"specialist": _v(who)})


def notification_latency(seconds: float) -> None:
    _notify_latency.record(max(seconds, 0.0))


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
        from opentelemetry.sdk.resources import Resource

        if isinstance(metrics.get_meter_provider(), MeterProvider):
            return False
        if exporter_factory is None:
            from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter

            exporter_factory = CloudMonitoringMetricsExporter
        from acme.llm import config as llm_config

        project_id = os.environ.get("ACME_GENAI_PROJECT") or llm_config.PROJECT
        reader = PeriodicExportingMetricReader(
            exporter_factory(project_id=project_id), export_interval_millis=30000
        )
        metrics.set_meter_provider(
            MeterProvider(
                metric_readers=[reader],
                resource=Resource.create({"service.name": service_name}),
            )
        )
    except Exception:  # noqa: BLE001 - metrics must never stop an engine from starting
        logging.getLogger(__name__).warning("Cloud Monitoring not configured", exc_info=True)
        return False
    return True
