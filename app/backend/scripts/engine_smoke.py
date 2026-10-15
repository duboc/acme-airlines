"""Clean-venv smoke test for the Agent Engine artifacts (#185). Run with the venv's python:

    python scripts/engine_smoke.py STAGED_DIR      # STAGED_DIR from `python -m acme.engines.stage`

Mimics container start-up: only the extracted `extra_packages` and the pip requirements are
available (the repo's `acme` is NOT installed); unpickle the engine, `set_up()` it and run a
query offline (LLM_MODE=fake). Fails loudly on a missing package, a package missing from the
tarball, a pickling problem or a `set_up()` exception.
"""

import asyncio
import os
import sys
from pathlib import Path

staged = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(staged / "extracted"))
os.environ.setdefault("LLM_MODE", "fake")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "493084803309")  # Agent Engine injects the NUMBER

import cloudpickle  # noqa: E402
from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter  # noqa: E402
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter  # noqa: E402

import acme  # noqa: E402

assert CloudMonitoringMetricsExporter and CloudTraceSpanExporter
assert Path(acme.__file__).resolve().is_relative_to(staged), f"acme came from {acme.__file__}"


def load(name: str):
    with open(staged / f"{name}.pkl", "rb") as fh:
        return cloudpickle.load(fh)


async def main() -> None:
    customer = load("customer")
    customer.set_up()
    diag = customer.diagnostics()
    assert diag["status"] == "ready", diag  # set_up() swallows errors into diagnostics (#185)
    events = [e async for e in customer.async_stream_query(message="poll", user_id="smoke")]
    assert events, "the Customer engine returned no events"
    ops = customer.register_operations()
    assert "a2a_skill" in ops["async"], ops
    sales = load("sales")  # set_up needs the live Customer engine, so only load + validate here
    assert hasattr(sales, "set_up") and hasattr(sales, "register_operations")
    await multi_worker()
    print("engine smoke ok: customer set_up + query, sales loads, 2 workers share state;",
          "acme from", acme.__file__)  # fmt: skip


def offer(jid: str) -> dict:
    from uuid import uuid4

    return dict(
        offer_id=str(uuid4()), journey_id=jid, flight="AC1252", cabin="premium_business",
        price="135.00", standard_price="290.00", net_margin="80.00", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip


async def multi_worker() -> None:
    """Agent Engine runs several processes per container (#200): simulate two of them over one
    shared state file and run scenarios 1-5 as sequential requests, alternating workers."""
    import tempfile

    os.environ["ACME_STATE_DB"] = str(Path(tempfile.mkdtemp()) / "state" / "state.db")
    a, b = load("customer"), load("customer")
    a.set_up()
    b.set_up()
    for w in (a, b):
        assert w.diagnostics()["status"] == "ready", w.diagnostics()
    a.scenario_start("happy-path")
    assert b.scenario_state()["scenario_id"] == "happy-path", "state not shared"
    for worker in (b, a, b, a):  # cancel, notify, reply 10am, reply thanks
        await worker.scenario_step()
    jid = b.scenario_state()["journey_id"]
    proposed = await a.a2a_skill("propose_offer", {"offer": offer(jid)})  # what Sales does
    assert proposed["status"] == "approved", proposed
    await b.whatsapp_action("P-MARIANA", "accept_offer")
    for w in (a, b):
        state = w.scenario_state()
        assert state["step_index"] == 4 and state["charged"] == "135.00", state
        detail = await w.journey_detail(jid)
        assert detail["status"] == "CLOSED" and detail["seat"]["seat"] == "2A", detail
    # a second scenario on the other worker starts clean and sequential turns keep working
    b.scenario_start("declines-upgrade")
    assert a.scenario_state()["scenario_id"] == "declines-upgrade"
    for worker in (a, b, a, b):
        await worker.scenario_step()
    jid2 = a.scenario_state()["journey_id"]
    await b.a2a_skill("propose_offer", {"offer": offer(jid2)})
    await a.scenario_step()
    assert b.scenario_state()["done"], b.scenario_state()


asyncio.run(main())
