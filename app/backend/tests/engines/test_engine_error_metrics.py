"""#344: the Customer engine counts its own server errors (`acme.http.server.errors`), since the
HTTP middleware only covers the API process."""

import pytest
from fastapi import HTTPException

from acme.api.errors import ApiError
from acme.engines.customer import EngineOperationError, build_customer_engine_app


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.delenv("ACME_STATE_DB", raising=False)
    app = build_customer_engine_app()
    app.set_up()
    return app


def count(metric_points, status):
    points = metric_points().get("acme.http.server.errors", [])
    return next((v for a, v in points if a == {"status_code": status}), 0)


async def test_an_unhandled_error_in_an_api_op_is_counted_as_a_500(engine, metric_points):
    async def boom(rt):
        raise RuntimeError("bug")

    before = count(metric_points, "500")
    out = await engine._api(boom)  # noqa: SLF001
    assert out["error"]["status"] == 500
    assert count(metric_points, "500") == before + 1


async def test_a_5xx_http_error_is_counted_with_its_status_and_a_4xx_is_not(engine, metric_points):
    async def unavailable(rt):
        raise HTTPException(503, "down")

    async def missing(rt):
        raise ApiError(404, "journey_not_found", "no such journey")

    b503, b500, b404 = (count(metric_points, s) for s in ("503", "500", "404"))
    assert (await engine._api(unavailable))["error"]["status"] == 503  # noqa: SLF001
    assert (await engine._api(missing))["error"]["status"] == 404  # noqa: SLF001
    assert count(metric_points, "503") == b503 + 1
    assert count(metric_points, "404") == b404 == 0 and count(metric_points, "500") == b500


def test_an_unexpected_failure_of_a_shared_op_is_counted_but_an_expected_one_is_not(
    engine, metric_points, monkeypatch
):
    before = count(metric_points, "500")

    def bug():
        raise RuntimeError("bug")

    monkeypatch.setattr(engine.runtime, "reset", bug)
    with pytest.raises(EngineOperationError):
        engine.reset()
    assert count(metric_points, "500") == before + 1
    with pytest.raises(EngineOperationError):  # an expected operation error: not a server fault
        engine.scenario_start("no-such-preset")
    assert count(metric_points, "500") == before + 1


def test_the_customer_service_wires_telemetry_at_start(monkeypatch):
    """#344: `acme-customer` never configured the metric provider, so its 5xx counter stayed a
    no-op. Both calls are no-ops unless the telemetry switch is set."""
    from acme.customer import main as customer_main

    calls = []
    monkeypatch.setattr(
        customer_main, "configure_cloud_trace", lambda name: calls.append(("t", name))
    )
    monkeypatch.setattr(
        customer_main, "configure_cloud_monitoring", lambda name: calls.append(("m", name))
    )
    monkeypatch.setenv("ACME_SALES_TOKEN", "x")
    monkeypatch.setattr(customer_main.asyncio, "run", lambda coro: coro.close())
    monkeypatch.setattr(customer_main, "create_app", lambda: object())
    customer_main.main()
    assert calls == [("t", "acme-irrops-demo"), ("m", "acme-irrops-demo")]
