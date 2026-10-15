"""#228: the Customer runtime acts on its own (OCC subscription + scheduled outreach and closure
driven by the Clock), with no API client connected; GET endpoints are read-only."""

import asyncio
import logging
from datetime import datetime, timedelta

import httpx
import pytest

from acme.api.app import create_app, create_local_app
from acme.customer.loop import CustomerLoop
from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, GRU
from acme.domain.journey import DisruptionStatus as S
from acme.llm.fake import FakeLlm

PID = "P-MARIANA"
NOTIFY_AT = datetime(2026, 10, 9, 22, 9, tzinfo=GRU)


def make():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    return rt, CustomerLoop(rt)


async def publish_to_occ(rt, flight="AC1234", reason="unscheduled maintenance"):
    """What the real OCC does: publish on the mock stream. Nobody calls the runtime directly."""
    await rt.client.call("occ", "flight_ops.publish_cancellation", flight=flight, reason=reason)


def outbound(rt):
    return [m for m in rt.messages.get(PID, []) if m.direction == "outbound"]


async def test_scenario_1_a_cancellation_on_the_occ_stream_is_ingested_without_any_client():
    rt, loop = make()
    await publish_to_occ(rt)
    assert rt.journeys == {}
    await loop.step()
    (rec,) = rt.journeys.values()
    assert rec.state.status is S.RESOLVED  # ingested, rebuilt, duty of care done
    assert outbound(rt) == []  # silent ingestion: quiet hours, nothing sent yet
    assert [e.type for e in rec.state.timeline][0] == "cancellation_detected"


async def test_scenario_3_the_proactive_whatsapp_goes_out_at_the_scheduled_time():
    rt, loop = make()
    await publish_to_occ(rt)
    await loop.step()
    rt.clock.advance(timedelta(minutes=2))  # 22:05
    await loop.step()
    assert outbound(rt) == []
    rt.clock.advance(NOTIFY_AT - rt.clock.now())  # 22:09: driven by the Clock, no GET involved
    await loop.step()
    (msg,) = outbound(rt)
    assert msg.timestamp == NOTIFY_AT and "AC1240" in msg.text
    await loop.step()
    assert len(outbound(rt)) == 1  # exactly one


async def test_the_loop_also_advances_service_closure_without_an_offer():
    rt, loop = make()
    await publish_to_occ(rt)
    await loop.step()
    rt.clock.advance(NOTIFY_AT - rt.clock.now())
    await loop.step()
    await rt.handle_inbound(PID, "Perfect, thanks!")
    (rec,) = rt.journeys.values()
    rt.clock.advance(rt.offer_window + timedelta(minutes=1))
    await loop.step()  # #229 closure now advances from the loop, not from a GET
    assert rec.state.status is S.CLOSED


async def test_the_running_loop_does_everything_in_the_background_and_shuts_down_cleanly():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    loop = CustomerLoop(rt, interval=0.01)
    loop.start()
    loop.start()  # idempotent: still one task
    assert loop.running
    await publish_to_occ(rt)
    for _ in range(300):
        if rt.journeys and next(iter(rt.journeys.values())).state.status is S.RESOLVED:
            break
        await asyncio.sleep(0.01)
    assert len(rt.journeys) == 1
    rt.clock.advance(NOTIFY_AT - rt.clock.now())
    for _ in range(300):
        if outbound(rt):
            break
        await asyncio.sleep(0.01)
    assert len(outbound(rt)) == 1
    await loop.stop()
    assert not loop.running
    await loop.stop()  # idempotent


async def test_failures_are_logged_and_retried_with_exponential_backoff(caplog):
    rt, _ = make()
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 6:
            raise asyncio.CancelledError

    loop = CustomerLoop(rt, interval=1.0, backoff_base=1.0, backoff_cap=4.0, sleep=fake_sleep,
                        jitter=lambda: 1.0)  # fmt: skip
    calls = {"n": 0}
    real_tick = rt.tick

    async def flaky():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise ConnectionError("occ unreachable for passenger joana.silva@example.com")
        await real_tick()

    rt.tick = flaky
    caplog.set_level(logging.WARNING)
    with pytest.raises(asyncio.CancelledError):
        await loop.run()
    assert sleeps[:3] == [1.0, 2.0, 4.0]  # backoff doubles up to the cap
    assert sleeps[3:] == [1.0, 1.0, 1.0]  # a success resets to the normal interval
    warnings = [r for r in caplog.records if "customer loop" in r.getMessage()]
    assert len(warnings) == 3
    assert "ConnectionError" in warnings[0].getMessage()
    assert "joana.silva" not in caplog.text  # nothing personal in the logs


async def test_the_loop_never_dies_on_an_unexpected_error():
    rt, _ = make()
    n = {"i": 0}

    async def boom():
        n["i"] += 1
        raise RuntimeError("always")

    rt.tick = boom
    loop = CustomerLoop(rt, interval=0.005, backoff_base=0.005, backoff_cap=0.01)
    loop.start()
    for _ in range(500):  # poll, do not assume the runner is fast: 5 s deadline
        if n["i"] >= 3:
            break
        await asyncio.sleep(0.01)
    assert loop.running and n["i"] >= 3  # still alive, still retrying
    await loop.stop()


async def test_get_endpoints_are_read_only():
    clock = FixedClock(DISRUPTION_DETECTED_AT)
    app = create_app(clock)
    rt = app.state.runtime
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/scenario/trigger-cancellation", json={"flight": "AC1234", "reason": "maintenance"}
        )
        (jid,) = r.json()["journey_ids"]
        clock.advance(NOTIFY_AT - clock.now())  # the notification is now due
        before = len(rt.journeys[jid].state.timeline)
        for url in (f"/whatsapp/{PID}/messages", f"/journeys/{jid}", "/journeys"):
            assert (await c.get(url)).status_code == 200
        assert (await c.get(f"/whatsapp/{PID}/messages")).json() == []  # no side effect
        assert len(rt.journeys[jid].state.timeline) == before
        await rt.tick()  # the explicit command (the loop's job) is what sends it
        assert len((await c.get(f"/whatsapp/{PID}/messages")).json()) == 1


async def test_the_local_app_runs_the_loop_for_the_lifetime_of_the_app(monkeypatch):
    monkeypatch.delenv("ACME_CUSTOMER_LOOP", raising=False)
    app = create_local_app(FixedClock(DISRUPTION_DETECTED_AT))
    async with app.router.lifespan_context(app):
        assert app.state.loop.running
    assert not app.state.loop.running  # clean shutdown with the app


async def test_the_loop_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("ACME_CUSTOMER_LOOP", "0")
    app = create_local_app(FixedClock(DISRUPTION_DETECTED_AT))
    async with app.router.lifespan_context(app):
        assert not app.state.loop.running
