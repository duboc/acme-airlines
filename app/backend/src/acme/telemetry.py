"""OpenTelemetry spans with a PII-free attribute allowlist.

`span(name, **attrs)` only accepts the allowlisted keys below, and only id/enum-like values
(never free text: no names, PNRs, phone numbers, emails or message bodies). A violation raises
in strict mode (tests, `ACME_TELEMETRY_STRICT=1`) and is dropped silently in production, so
tracing can never break a journey. Behaviour is identical in local mode and on Agent Engine
(where ADK's `enable_tracing=True` supplies the exporter); without a provider it is a no-op.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind

tracer = trace.get_tracer("acme")

ALLOWED_ATTRIBUTES = frozenset(
    {
        "acme.scenario",
        "acme.journey_id",
        "acme.flight",
        "acme.specialist",
        "acme.skill",
        "acme.guardrail.decision",
        "acme.guardrail.reason_code",
        "acme.offer.kind",
        "acme.step",
    }
)
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")


def is_safe_value(value: str) -> bool:
    return bool(_SAFE_VALUE.match(value))


_strict = os.environ.get("ACME_TELEMETRY_STRICT") == "1"


class TelemetryError(ValueError):
    """A non-allowlisted attribute key or a non-id value (strict mode only)."""


def set_strict(value: bool) -> None:
    global _strict
    _strict = value


def _clean(attrs: dict[str, object]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in attrs.items():
        if value is None:
            continue
        key = key.replace("__", ".")
        text = str(value)
        if key not in ALLOWED_ATTRIBUTES or not is_safe_value(text):
            if _strict:
                raise TelemetryError(f"attribute not allowed: {key!r}")  # never echo the value
            continue
        clean[key] = text
    return clean


@contextmanager
def span(name: str, *, kind: SpanKind = SpanKind.INTERNAL, **attrs: object) -> Iterator[Span]:
    """Start a span. Keyword `__` stands for `.`: `span("x", acme__guardrail__decision="approve")`
    (or pass dotted keys via `**{"acme.flight": "AC1252"}`)."""
    clean = _clean(attrs)
    with tracer.start_as_current_span(name, kind=kind, attributes=clean) as s:
        yield s


def flush(timeout_ms: int = 5000) -> None:
    """Export buffered spans now (#215).

    A BatchSpanProcessor exports on a timer, but an Agent Engine process can be throttled between
    requests, so the spans of the last operation of a burst may sit in the buffer for a long time
    (the live proof never saw them). Every engine operation calls this before it returns. A no-op
    without an SDK provider; a failing flush never breaks an operation.
    """
    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    if force_flush is None:
        return
    try:
        force_flush(timeout_ms)
    except Exception:  # noqa: BLE001 - tracing must never break a journey
        logging.getLogger(__name__).debug("span flush failed", exc_info=True)


def set_attributes(s: Span, **attrs: object) -> None:
    """Set allowlisted attributes on a live span (same rules as `span()`)."""
    s.set_attributes(_clean(attrs))


def specialist_span(name: str, journey_id: str):
    """`acme.customer.specialist.<name>` around one specialist invocation."""
    return span(
        f"acme.customer.specialist.{name}", acme__specialist=name, acme__journey_id=journey_id
    )


# --- Cloud Trace export for the custom-template engines (#163) ---------------------------------
TELEMETRY_ENV = "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY"  # SDK flag (reserved prefix)
TRACING_ENV = "ACME_ENABLE_TRACING"  # ours: what the deploy sets (#190)


def configure_cloud_trace(service_name: str, exporter_factory=None) -> bool:
    """Install a Cloud Trace exporter when the engine asks for telemetry and none exists yet.

    The SDK only wires `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY` into its `AdkApp` template;
    our engines are custom templates, so without this their spans would never be exported.
    `service.name` is the engine display name (the Observatory filters traces on it). Never
    replaces an existing SDK provider. Returns True when a provider was installed.
    """
    if not any(
        os.environ.get(v, "").lower() in ("true", "1") for v in (TRACING_ENV, TELEMETRY_ENV)
    ):
        return False
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        if isinstance(trace.get_tracer_provider(), TracerProvider):
            return False
        if exporter_factory is None:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            exporter_factory = CloudTraceSpanExporter  # ADC; the runtime SA has cloudtrace.agent
        from acme.llm import config as llm_config

        # Always the project ID: the injected GOOGLE_CLOUD_PROJECT is the project number, which
        # Cloud Trace rejects ("400 Invalid project id in name", #203).
        project_id = os.environ.get("ACME_GENAI_PROJECT") or llm_config.PROJECT
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(exporter_factory(project_id=project_id)))
        trace.set_tracer_provider(provider)
    except Exception:  # noqa: BLE001 - tracing must never stop an engine from starting
        logging.getLogger(__name__).warning("Cloud Trace export not configured", exc_info=True)
        return False
    return True
