"""Unit tests for acme-factory-watchdog (TDD: RED -> GREEN)."""
import json
from pathlib import Path

import pytest
from main import (
    build_handoff,
    compute_fingerprint,
    decide_action,
    execute_action,
)


def test_fingerprint_ignores_factory_status_issue():
    issues_a = [
        {"number": 39, "title": "🏭 Factory status", "updated_at": "2026-10-13T01:00:00Z", "labels": []},
        {"number": 200, "title": "Shared state", "updated_at": "2026-10-13T01:00:00Z", "labels": [{"name": "status:ready"}]},
    ]
    issues_b = [
        {"number": 39, "title": "🏭 Factory status", "updated_at": "2026-10-13T02:30:00Z", "labels": []},
        {"number": 200, "title": "Shared state", "updated_at": "2026-10-13T01:00:00Z", "labels": [{"name": "status:ready"}]},
    ]
    prs = [{"number": 206, "updated_at": "2026-10-13T01:10:00Z", "head": {"sha": "abc"}}]
    runs = [{"name": "CI", "head_sha": "def", "status": "completed", "conclusion": "success"}]
    agents = [
        {"slug": "acme-lead", "phase": "running", "activity": "blocked"},
        {"slug": "acme-backend", "phase": "suspended", "activity": ""},
    ]
    assert compute_fingerprint(issues_a, prs, runs, agents) == compute_fingerprint(issues_b, prs, runs, agents)


def test_fingerprint_changes_on_issue_pr_run_or_worker_change():
    base_issues = [{"number": 200, "title": "Shared state", "updated_at": "t1", "labels": [{"name": "status:ready"}]}]
    base_prs = [{"number": 206, "updated_at": "t1", "head": {"sha": "s1"}}]
    base_runs = [{"name": "CI", "head_sha": "m1", "status": "completed", "conclusion": "success"}]
    base_agents = [{"slug": "acme-backend", "phase": "running", "activity": "working"}]
    fp0 = compute_fingerprint(base_issues, base_prs, base_runs, base_agents)

    assert compute_fingerprint(
        [{"number": 200, "title": "Shared state", "updated_at": "t2", "labels": [{"name": "status:in-review"}]}],
        base_prs, base_runs, base_agents,
    ) != fp0
    assert compute_fingerprint(
        base_issues,
        [{"number": 206, "updated_at": "t2", "head": {"sha": "s2"}}],
        base_runs, base_agents,
    ) != fp0
    assert compute_fingerprint(
        base_issues, base_prs,
        [{"name": "CI", "head_sha": "m1", "status": "completed", "conclusion": "failure"}],
        base_agents,
    ) != fp0
    assert compute_fingerprint(
        base_issues, base_prs, base_runs,
        [{"slug": "acme-backend", "phase": "running", "activity": "completed"}],
    ) != fp0


def test_decide_never_resumes_suspended_lead():
    lead = {"slug": "acme-lead", "phase": "suspended", "activity": ""}
    res = decide_action(lead, "fp-new", {"fingerprint": "fp-old", "unhealthy_since": 100}, now=1000)
    assert res["action"] == "noop"
    assert res["alert_po"] is False
    assert "paused" in res["reason"]
    assert res["new_state"]["unhealthy_since"] is None


def test_decide_missing_lead_alerts_po():
    res = decide_action(None, "fp1", {}, now=1000)
    assert res["action"] == "noop"
    assert res["alert_po"] is True


def test_decide_idle_no_change_is_noop():
    lead = {"slug": "acme-lead", "phase": "running", "activity": "blocked"}
    res = decide_action(lead, "fp1", {"fingerprint": "fp1", "last_wake_ts": 100}, now=1000)
    assert res["action"] == "noop"
    assert res["alert_po"] is False


def test_decide_idle_with_change_wakes_and_rate_limits():
    lead = {"slug": "acme-lead", "phase": "running", "activity": "blocked"}
    r1 = decide_action(lead, "fp2", {"fingerprint": "fp1", "last_wake_ts": 100}, now=1000, min_wake_interval=600)
    assert r1["action"] == "wake"
    assert r1["new_state"]["fingerprint"] == "fp2"
    assert r1["new_state"]["last_wake_ts"] == 1000

    # Immediate subsequent change within cooldown is rate-limited and keeps fp2 so it fires after cooldown.
    r2 = decide_action(lead, "fp3", r1["new_state"], now=1200, min_wake_interval=600)
    assert r2["action"] == "noop"
    assert r2["new_state"]["fingerprint"] == "fp2"

    # Once cooldown passes, fp3 wakes the lead.
    r3 = decide_action(lead, "fp3", r2["new_state"], now=1601, min_wake_interval=600)
    assert r3["action"] == "wake"
    assert r3["new_state"]["fingerprint"] == "fp3"


def test_decide_busy_lead_not_interrupted_and_preserves_fingerprint():
    lead = {"slug": "acme-lead", "phase": "running", "activity": "working"}
    res = decide_action(lead, "fp-new", {"fingerprint": "fp-old", "last_wake_ts": 0}, now=1000)
    assert res["action"] == "noop"
    assert res["new_state"]["fingerprint"] == "fp-old"


def test_decide_stalled_lead_messages_then_reincarnates_and_alerts():
    stalled = {"slug": "acme-lead", "phase": "running", "activity": "stalled"}
    r1 = decide_action(stalled, "fp1", {}, now=1000, stall_escalate_sec=600)
    assert r1["action"] == "recover_message"
    assert r1["alert_po"] is False
    assert r1["new_state"]["unhealthy_since"] == 1000

    # Still stalled 5 minutes later -> wait for first recovery before escalating.
    r2 = decide_action(stalled, "fp1", r1["new_state"], now=1300, stall_escalate_sec=600)
    assert r2["action"] == "noop"
    assert r2["alert_po"] is False

    # Still stalled after 10 minutes -> reincarnate and alert the PO.
    r3 = decide_action(stalled, "fp1", r2["new_state"], now=1600, stall_escalate_sec=600)
    assert r3["action"] == "reincarnate"
    assert r3["alert_po"] is True


def test_decide_errored_lead_starts_then_reincarnates():
    errored = {"slug": "acme-lead", "phase": "error", "activity": "crashed"}
    r1 = decide_action(errored, "fp1", {}, now=1000, stall_escalate_sec=600)
    assert r1["action"] == "recover_start"
    assert r1["alert_po"] is False

    r2 = decide_action(errored, "fp1", r1["new_state"], now=1700, stall_escalate_sec=600)
    assert r2["action"] == "reincarnate"
    assert r2["alert_po"] is True


def test_build_handoff_contains_template_sections_and_status():
    agents = [
        {"slug": "acme-lead", "phase": "running", "activity": "blocked"},
        {"slug": "acme-backend", "phase": "running", "activity": "working"},
    ]
    text = build_handoff("# 🏭 Factory status\nOpen: 2", agents, "2026-10-13T04:00:00Z")
    for heading in (
        "# Reincarnation handoff",
        "## Role charter",
        "## Immediate active work (status, next action)",
        "## Canonical files and artifacts",
        "## Authority and ownership (who to ask, who can approve)",
        "## Children agents and their state",
        "## Operating constraints and lessons learned",
        "## Do not redo",
    ):
        assert heading in text
    assert "acme-backend: phase=running activity=working" in text
    assert "Open: 2" in text


def test_execute_action_invokes_expected_scion_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HUB_ENDPOINT", "http://10.0.0.2:8080")
    monkeypatch.setenv("HUB_PROJECT", "duboc-acme-airlines")
    monkeypatch.setenv("SCION_HUB_TOKEN", "scion_pat_test")
    calls = []

    class Res:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, **kw):
        calls.append(args)
        return Res()

    execute_action("wake", "state changed", handoff_text="h", run=fake_run, tmp_dir=tmp_path)
    assert calls[-1][:4] == ["scion", "message", "acme-lead", "--project"]

    execute_action("recover_start", "lead error", handoff_text="h", run=fake_run, tmp_dir=tmp_path)
    assert calls[-1][:3] == ["scion", "resume", "acme-lead"]

    execute_action("reincarnate", "daily", handoff_text="# Reincarnation handoff\n", run=fake_run, tmp_dir=tmp_path)
    assert calls[-1][:4] == ["scion", "reincarnate", "acme-lead", "--project"]
    assert "--handoff-file" in calls[-1]
