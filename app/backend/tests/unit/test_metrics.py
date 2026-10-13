from acme import metrics


def _by(points, name):
    return {tuple(sorted(a.items())): v for a, v in points().get(name, [])}


def test_guardrail_rejection_counts_each_rule(metric_points):
    base = _by(metric_points, "acme.guardrail.decisions")
    metrics.guardrail_decision(False, ["no_inventory", "negative_margin"], "propose")
    metrics.guardrail_decision(True, ["a"], "send")
    now = _by(metric_points, "acme.guardrail.decisions")
    k = lambda rule, out, step: (("outcome", out), ("rule", rule), ("step", step))  # noqa: E731
    assert (
        now[k("no_inventory", "reject", "propose")]
        - base.get(k("no_inventory", "reject", "propose"), 0)
        == 1
    )
    assert now[k("all", "approve", "send")] - base.get(k("all", "approve", "send"), 0) == 1


def test_unsafe_attribute_values_never_leak(metric_points):
    metrics.template_fallback("Mariana Silva")
    pts = metric_points()["acme.llm.template_fallbacks"]
    assert all("Mariana" not in str(a) for a, _ in pts)
    assert any(a == {"surface": "unknown"} for a, _ in pts)


def test_needs_human_uses_specialist_or_step_prefix(metric_points):
    metrics.needs_human({"step": "transfer:hotel_to_airport", "error": "boom"})
    assert any(a == {"specialist": "transfer"} for a, _ in metric_points()["acme.needs_human"])


def test_notification_latency_is_recorded_in_seconds(metric_points):
    metrics.notification_latency(42.0)
    assert any(v >= 1 for _, v in metric_points()["acme.notification.latency"])


def test_cloud_monitoring_is_off_without_the_tracing_switch(monkeypatch):
    monkeypatch.delenv("ACME_ENABLE_TRACING", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", raising=False)
    assert metrics.configure_cloud_monitoring("x") is False
