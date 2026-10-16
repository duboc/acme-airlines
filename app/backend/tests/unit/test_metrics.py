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


def test_rebuild_duration_is_recorded_with_preset_and_reason(metric_points):
    metrics.rebuild_duration(0.5, "happy-path", "preset_mismatch")
    metrics.rebuild_duration(0.1, "bad value with spaces!", "step_mismatch")
    pts = metric_points()["acme.scenario.rebuild.duration"]
    assert any(a == {"preset": "happy-path", "reason": "preset_mismatch"} for a, _ in pts)
    assert any(a["preset"] == "unknown" for a, _ in pts)


# --- #337 and #344 ---------------------------------------------------------------------------


def test_the_rebuild_histogram_has_sub_second_buckets():
    """#344: with the SDK's default boundaries every rebuild landed in (0, 5] and Cloud Monitoring
    reported p50 2.5 s / p95 4.75 s for rebuilds of 0.3 s."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    histogram = provider.get_meter("t").create_histogram(
        "h", unit="s", explicit_bucket_boundaries_advisory=list(metrics.REBUILD_BUCKETS_S)
    )
    histogram.record(0.35)
    (rm,) = reader.get_metrics_data().resource_metrics
    (point,) = rm.scope_metrics[0].metrics[0].data.data_points
    assert point.explicit_bounds == metrics.REBUILD_BUCKETS_S
    assert point.bucket_counts[metrics.REBUILD_BUCKETS_S.index(0.4)] == 1  # the (0.3, 0.4] bucket
    assert metrics._rebuild_duration._advisory.explicit_bucket_boundaries == list(  # noqa: SLF001
        metrics.REBUILD_BUCKETS_S
    )


def test_the_provider_drops_the_sdk_self_metrics_and_keeps_ours():
    """#337: `otel.sdk.metric_reader.collection.duration` made Cloud Monitoring answer 400."""
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = metrics.build_meter_provider("svc", [reader])
    meter = provider.get_meter("t")
    meter.create_counter("otel.sdk.metric_reader.collection.duration").add(1)
    meter.create_counter("acme.offers").add(1)
    names = {
        m.name
        for rm in reader.get_metrics_data().resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    }
    assert names == {"acme.offers"}


def test_the_default_exporter_gets_a_unique_identifier_per_process(monkeypatch):
    """#337: the worker processes of one container export the same series; without
    `add_unique_identifier` Cloud Monitoring rejects the collisions."""
    import sys
    import types

    seen = {}

    from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult

    class Exporter(MetricExporter):
        def __init__(self, **kwargs):
            super().__init__()
            seen.update(kwargs)

        def export(self, metrics_data, timeout_millis=10_000, **kwargs):
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis=10_000):
            return True

        def shutdown(self, timeout_millis=30_000, **kwargs):
            pass

    fake = types.ModuleType("opentelemetry.exporter.cloud_monitoring")
    fake.CloudMonitoringMetricsExporter = Exporter
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.cloud_monitoring", fake)
    monkeypatch.setenv("ACME_ENABLE_TRACING", "true")
    # The test session already has an SDK provider, so configure returns before building: build
    # the reader the way it does, through the same default factory path.
    from opentelemetry.sdk.metrics import MeterProvider

    monkeypatch.setattr(metrics.metrics, "get_meter_provider", lambda: object())
    monkeypatch.setattr(
        metrics.metrics, "set_meter_provider", lambda provider: seen.update(p=provider)
    )
    monkeypatch.setattr(metrics, "seed_counters", lambda: seen.update(seeded=True))
    assert metrics.configure_cloud_monitoring("svc") is True
    assert seen["add_unique_identifier"] is True and seen["project_id"]
    assert isinstance(seen["p"], MeterProvider) and seen["seeded"] is True


def test_the_http_error_counter_is_exported_from_the_start(metric_points):
    metrics.seed_counters()
    pts = metric_points()["acme.http.server.errors"]
    assert any(a == {"status_code": "500"} for a, _ in pts)  # at 0 if nothing failed yet


def test_ack_veto_counts_once_live_and_nothing_during_a_replay(metric_points):
    from acme.replay import replaying

    def total():
        return sum(v for _, v in metric_points().get("acme.llm.ack_vetoes", []))

    before = total()
    metrics.ack_veto()
    assert total() == before + 1
    with replaying():
        metrics.ack_veto()
    assert total() == before + 1
    assert all(a == {} for a, _ in metric_points()["acme.llm.ack_vetoes"])  # no passenger text
