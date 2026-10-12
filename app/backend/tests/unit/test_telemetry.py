import pytest

from acme import telemetry


def test_allowlisted_attributes_are_recorded(spans):
    with telemetry.span(
        "acme.x", acme__flight="AC1252", acme__guardrail__reason_code="no_inventory"
    ):
        pass
    (s,) = spans.get_finished_spans()
    assert dict(s.attributes) == {
        "acme.flight": "AC1252",
        "acme.guardrail.reason_code": "no_inventory",
    }


def test_unknown_key_raises_in_strict_mode(spans):
    with pytest.raises(telemetry.TelemetryError), telemetry.span("acme.x", passenger="p"):
        pass


def test_free_text_value_raises_in_strict_mode_without_echoing_it(spans):
    with pytest.raises(telemetry.TelemetryError) as exc:
        with telemetry.span("acme.x", acme__step="Mariana Silva"):
            pass
    assert "Mariana" not in str(exc.value)


def test_production_drops_bad_attributes_and_keeps_the_span(spans):
    telemetry.set_strict(False)
    with telemetry.span("acme.x", passenger="p", acme__step="Mariana Silva", acme__flight="AC1"):
        pass
    (s,) = spans.get_finished_spans()
    assert dict(s.attributes) == {"acme.flight": "AC1"}


def test_none_values_are_dropped(spans):
    with telemetry.span("acme.x", acme__journey_id=None, acme__flight="AC1"):
        pass
    (s,) = spans.get_finished_spans()
    assert dict(s.attributes) == {"acme.flight": "AC1"}
