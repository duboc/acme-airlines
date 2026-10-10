"""Tests for the budget guard decision logic (run: uv run --with pytest pytest -q infra/budget-guard)."""
import base64
import json

import main


def envelope(payload: dict) -> dict:
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"message": {"data": data, "attributes": {"budgetId": "b-1"}}, "subscription": "s"}


def test_under_budget_does_not_stop():
    n = main.parse_notification(envelope({"costAmount": 499.0, "budgetAmount": 1000.0}))
    assert main.should_stop(n) is False


def test_at_budget_stops():
    n = main.parse_notification(envelope({"costAmount": 1000.0, "budgetAmount": 1000.0}))
    assert main.should_stop(n) is True


def test_over_budget_stops():
    n = main.parse_notification(envelope({"costAmount": 1250.5, "budgetAmount": 1000.0}))
    assert main.should_stop(n) is True


def test_threshold_alone_does_not_stop_when_cost_below_budget():
    # alertThresholdExceeded=1.0 can be reported for *forecasted* spend; act on actual cost only.
    n = main.parse_notification(envelope({"costAmount": 900.0, "budgetAmount": 1000.0, "alertThresholdExceeded": 1.0}))
    assert main.should_stop(n) is False


def test_zero_or_missing_budget_never_stops():
    assert main.should_stop(main.parse_notification(envelope({"costAmount": 5.0, "budgetAmount": 0}))) is False
    assert main.should_stop(main.parse_notification(envelope({"costAmount": 5.0}))) is False


def test_malformed_envelope_is_ignored():
    assert main.parse_notification({"message": {"data": "not-base64!!"}}) == {}
    assert main.should_stop({}) is False
