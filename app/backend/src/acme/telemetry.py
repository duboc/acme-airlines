"""OpenTelemetry spans with a PII-free attribute allowlist.

`span(name, **attrs)` only accepts the allowlisted keys below, and only id/enum-like values
(never free text: no names, PNRs, phone numbers, emails or message bodies). A violation raises
in strict mode (tests, `ACME_TELEMETRY_STRICT=1`) and is dropped silently in production, so
tracing can never break a journey. Behaviour is identical in local mode and on Agent Engine
(where ADK's `enable_tracing=True` supplies the exporter); without a provider it is a no-op.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span

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
def span(name: str, **attrs: object) -> Iterator[Span]:
    """Start a span. Keyword `__` stands for `.`: `span("x", acme__guardrail__decision="approve")`
    (or pass dotted keys via `**{"acme.flight": "AC1252"}`)."""
    clean = _clean(attrs)
    with tracer.start_as_current_span(name, attributes=clean) as s:
        yield s


def set_attributes(s: Span, **attrs: object) -> None:
    """Set allowlisted attributes on a live span (same rules as `span()`)."""
    s.set_attributes(_clean(attrs))


def specialist_span(name: str, journey_id: str):
    """`acme.customer.specialist.<name>` around one specialist invocation."""
    return span(
        f"acme.customer.specialist.{name}", acme__specialist=name, acme__journey_id=journey_id
    )
