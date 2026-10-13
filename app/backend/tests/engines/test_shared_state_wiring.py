"""#200 part 2: the Customer engine's operations share state across workers (processes)."""

import json
import subprocess
import sys
import textwrap
from datetime import datetime
from uuid import uuid4

import pytest

from acme.domain.fixtures import GRU
from acme.engines.customer import EngineOperationError, build_customer_engine_app

PID = "P-MARIANA"
NOTIFY_AT = datetime(2026, 10, 9, 22, 9, tzinfo=GRU)


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    path = tmp_path / "state" / "s.db"
    monkeypatch.setenv("ACME_STATE_DB", str(path))
    return path


def worker():
    app = build_customer_engine_app()
    app.set_up()
    assert app.diagnostics()["status"] == "ready", app.diagnostics()
    return app


def offer(jid):
    return dict(
        offer_id=str(uuid4()), journey_id=jid, flight="AC1252", cabin="premium_business",
        price="135.00", standard_price="290.00", net_margin="80.00", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip


async def to_sent_offer(a, b):
    """happy-path up to a delivered offer, alternating workers like Agent Engine routing would."""
    a.scenario_start("happy-path")
    await b.scenario_step()  # cancel
    await a.scenario_step()  # notify (22:09)
    await b.scenario_step()  # reply: after 10am
    await a.scenario_step()  # reply: thanks
    jid = b.scenario_state()["journey_id"]
    out = await a.a2a_skill("propose_offer", {"offer": offer(jid)})  # what Sales would do
    assert out["status"] == "approved", out
    return jid


async def test_scenario_started_on_one_worker_is_visible_to_the_others(state_env):
    a, b = worker(), worker()
    a.scenario_start("happy-path")
    assert b.scenario_state()["scenario_id"] == "happy-path"  # was "no scenario is started"
    await b.scenario_step()
    assert a.scenario_state()["step_index"] == 1
    assert len(a.journeys_list()) == len(b.journeys_list()) == 1


async def test_scenario_1_to_5_across_alternating_workers_never_loses_state(state_env):
    a, b = worker(), worker()
    jid = await to_sent_offer(a, b)
    await b.whatsapp_action(PID, "accept_offer")  # the accept step lands on the other worker
    for w in (a, b):
        state = w.scenario_state()
        assert state["step_index"] == 4 and state["charged"] == "135.00"
        detail = await w.journey_detail(jid)
        assert detail["status"] == "CLOSED" and detail["seat"]["seat"] == "2A"
        assert detail["offer"]["status"] == "accepted"
        events = [e["type"] for e in w.journey_events(jid)]
        assert events.count("upgrade_confirmed") == 1 and events.count("seat_released") == 1


async def test_reset_on_one_worker_is_seen_by_the_other(state_env):
    a, b = worker(), worker()
    a.scenario_start("happy-path")
    await a.scenario_step()
    assert len(b.journeys_list()) == 1
    b.reset()
    assert a.journeys_list() == []


async def test_each_operation_catches_up_with_writes_made_by_another_worker(state_env):
    a, b = worker(), worker()
    a.scenario_start("happy-path")
    await a.scenario_step()
    await b.scenario_step()  # b was behind: it must load a's write first
    await a.scenario_step()
    assert a.scenario_state()["step_index"] == b.scenario_state()["step_index"] == 3


async def test_without_a_state_db_workers_are_independent_as_before(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ACME_STATE_DB", raising=False)
    a, b = worker(), worker()
    a.scenario_start("happy-path")
    with pytest.raises(EngineOperationError, match="no scenario is started"):
        b.scenario_state()


def run_in_process(code: str, state_path, *args: str) -> subprocess.Popen:
    import os

    env = {**os.environ, "ACME_STATE_DB": str(state_path), "PYTHONWARNINGS": "ignore"}
    env.pop("LLM_MODE", None)
    return subprocess.Popen(
        [sys.executable, "-W", "ignore", "-c", textwrap.dedent(code), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )  # fmt: skip


ACCEPT_CODE = """
import asyncio, json
from acme.engines.customer import build_customer_engine_app
app = build_customer_engine_app(); app.set_up()
print(json.dumps(asyncio.run(app.whatsapp_action("P-MARIANA", "accept_offer"))))
"""

TICK_CODE = """
import asyncio, json, sys
from acme.engines.customer import build_customer_engine_app
app = build_customer_engine_app(); app.set_up()
detail = asyncio.run(app.journey_detail(sys.argv[1]))
print(json.dumps({"status": detail["status"]}))
"""


async def test_accept_race_across_real_processes_charges_once(state_env):
    """#88 across processes: 4 processes tap Accept at once; one charge, one 2A, one release."""
    a, b = worker(), worker()
    jid = await to_sent_offer(a, b)
    procs = [run_in_process(ACCEPT_CODE, state_env) for _ in range(4)]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0, err[-2000:]
        results.append(json.loads(out.strip().splitlines()[-1]))
    assert {r["offer_status"] for r in results} == {"accepted"}  # idempotent for the losers
    c = worker()  # a fresh worker reads the final shared state (its first operation loads it)
    detail = await c.journey_detail(jid)
    partners = c.runtime.sim.partners
    assert len(partners.charges) == 1 and str(partners.net_charged("MSLV42")) == "135.00"
    assert detail["seat"]["seat"] == "2A" and detail["status"] == "CLOSED"
    events = [e["type"] for e in c.journey_events(jid)]
    assert events.count("upgrade_confirmed") == 1 and events.count("seat_released") == 1
    assert events.count("accept_offer") >= 1


async def test_duplicate_notification_across_real_processes_sends_one_message(state_env):
    """#97 across processes: 4 processes poll (journey_detail ticks) at 22:09; one message."""
    a = worker()
    a.scenario_start("happy-path")
    await a.scenario_step()  # cancel: journey RESOLVED, notification due at 22:09
    jid = a.scenario_state()["journey_id"]
    async with a.shared.operation(a.runtime, a.scenarios):
        a.runtime.clock.advance(NOTIFY_AT - a.runtime.clock.now())
    procs = [run_in_process(TICK_CODE, state_env, jid) for _ in range(4)]
    for p in procs:
        _, err = p.communicate(timeout=180)
        assert p.returncode == 0, err[-2000:]
    c = worker()
    events = [e["type"] for e in c.journey_events(jid)]
    assert events.count("notification_sent") == 1
    outbound = [m for m in (await c.whatsapp_messages(PID)) if m["direction"] == "outbound"]
    assert len(outbound) == 1
