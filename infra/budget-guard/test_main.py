"""Tests for the budget guard decision logic (run: uv run --with pytest pytest -q infra/budget-guard)."""
import base64
import json

import main


def envelope(payload: dict) -> dict:
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"message": {"data": data, "attributes": {"budgetId": "b-1"}}, "subscription": "s"}


def test_under_budget_does_not_pause():
    n = main.parse_notification(envelope({"costAmount": 499.0, "budgetAmount": 1000.0}))
    assert main.should_pause(n) is False


def test_at_budget_pauses():
    n = main.parse_notification(envelope({"costAmount": 1000.0, "budgetAmount": 1000.0}))
    assert main.should_pause(n) is True


def test_over_budget_pauses():
    n = main.parse_notification(envelope({"costAmount": 1250.5, "budgetAmount": 1000.0}))
    assert main.should_pause(n) is True


def test_threshold_alone_does_not_pause_when_cost_below_budget():
    # alertThresholdExceeded=1.0 can be reported for *forecasted* spend; act on actual cost only.
    n = main.parse_notification(envelope({"costAmount": 900.0, "budgetAmount": 1000.0, "alertThresholdExceeded": 1.0}))
    assert main.should_pause(n) is False


def test_zero_or_missing_budget_never_pauses():
    assert main.should_pause(main.parse_notification(envelope({"costAmount": 5.0, "budgetAmount": 0}))) is False
    assert main.should_pause(main.parse_notification(envelope({"costAmount": 5.0}))) is False


def test_malformed_envelope_is_ignored():
    assert main.parse_notification({"message": {"data": "not-base64!!"}}) == {}
    assert main.should_pause({}) is False


class FakeRun:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, args, env=None, capture_output=None, text=None, timeout=None):
        self.calls.append((args, env))
        class R:  # minimal CompletedProcess
            returncode, stdout, stderr = self.rc, "suspended 3 agents", ""
        return R()


def test_pause_factory_suspends_all_agents_via_hub(monkeypatch):
    monkeypatch.setenv("HUB_ENDPOINT", "http://10.128.0.2:8080")
    monkeypatch.setenv("SCION_HUB_TOKEN", "scion_pat_test")
    monkeypatch.setenv("HUB_PROJECT", "duboc-acme-airlines")
    run = FakeRun()
    assert main.pause_factory(run) == "agents suspended"
    args, env = run.calls[0]
    assert args == ["scion", "suspend", "--all", "--project", "duboc-acme-airlines"]
    assert env["SCION_HUB_ENDPOINT"] == "http://10.128.0.2:8080"
    assert env["SCION_HUB_TOKEN"] == "scion_pat_test"


def test_pause_factory_failure_raises(monkeypatch):
    monkeypatch.setenv("HUB_ENDPOINT", "http://h:8080")
    monkeypatch.setenv("SCION_HUB_TOKEN", "t")
    monkeypatch.setenv("HUB_PROJECT", "p")
    import pytest
    with pytest.raises(RuntimeError):
        main.pause_factory(FakeRun(rc=1))


def test_guard_never_touches_compute():
    # Product owner rule: never stop the VM. The guard must not call the Compute API.
    src = open(main.__file__).read()
    assert "compute.googleapis.com" not in src and "/stop" not in src
