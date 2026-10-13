"""Exceptions inside spans must not leak passenger PII to the exporter (#258)."""

import pytest
from opentelemetry.trace import StatusCode

from acme import telemetry
from acme.logging_setup import MASK

PII = ["Mariana Souza", "mariana@example.com", "+55 11 91234-5678", "P-MARIANA", "FFP-123456"]


def _boom():
    raise RuntimeError("tool failed for " + "; ".join(PII))


def _dump(finished) -> str:
    parts = [str(finished.status.description)]
    for ev in finished.events:
        parts += [ev.name, *map(str, ev.attributes.values())]
    parts += map(str, finished.attributes.values())
    return "\n".join(parts)


@pytest.mark.parametrize("kind", ["span", "engine_op"])
def test_exception_event_is_redacted(spans, kind):
    def ctx():
        if kind == "span":
            return telemetry.span("acme.test")
        return telemetry.EngineOp("probe").span()

    with pytest.raises(RuntimeError), ctx():
        _boom()
    (finished,) = spans.get_finished_spans()
    text = _dump(finished)
    for item in PII:
        assert item not in text
    (event,) = [e for e in finished.events if e.name == "exception"]
    assert event.attributes["exception.type"] == "RuntimeError"
    assert MASK in event.attributes["exception.message"]
    assert MASK in event.attributes["exception.stacktrace"]
    assert finished.status.status_code is StatusCode.ERROR
