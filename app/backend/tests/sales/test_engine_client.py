"""Sales over the Customer engine transport (#161): fake handle, no GCP."""

import inspect

import pytest

from acme.engines.sales import DISPLAY_NAME, build_sales_engine_app
from acme.sales.client import (
    A2ACustomerClient,
    A2AError,
    EngineCustomerClient,
    build_customer_client,
)
from tests.governance.flow import Flow, engine_over, offer_payload

PII = {"name", "phone", "pnr", "pnr_locator", "passenger_id", "email"}


def public(cls):
    return {n for n, _ in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}


def test_client_exposes_exactly_the_three_skills():
    assert public(EngineCustomerClient) == {"list_journeys", "journey_status", "propose_offer"}


@pytest.mark.parametrize(
    "reply",
    [
        {"error": {"code": -32602, "message": "nope"}},
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "nope"}},
    ],
)
async def test_both_error_shapes_raise(reply):
    class Handle:
        async def a2a_skill(self, skill, args):
            return reply

    with pytest.raises(A2AError, match="nope"):
        await EngineCustomerClient(Handle()).list_journeys()


async def test_scenario_outcomes_equal_across_transports():
    outcomes = []
    for transport in ("http", "engine"):
        f = Flow(transport)
        await f.acknowledged_replan()
        outcomes.append(
            (
                [e.type for e in f.state.timeline],
                [(o.status, o.offer.price) for o in f.state.offers],
                len(f.offer_messages()),
            )
        )
        await f.http.aclose()
    assert outcomes[0] == outcomes[1] and outcomes[0][2] == 1


async def test_engine_rejects_non_allowlisted_and_free_text_without_pii():
    f = Flow("engine")
    await f.cancel()
    engine = engine_over(f.rt)
    before = [r.state.offers for r in f.rt.journeys.values()]
    for skill, args in [
        ("get_profile", {"passenger_id": "P-MARIANA"}),
        ("journey_status", {"journey_id": f.jid, "note": "buy now"}),
        ("propose_offer", {"offer": offer_payload(f.jid, pitch="Treat yourself!")}),
    ]:
        assert "error" in await engine.a2a_skill(skill, args)
    assert [r.state.offers for r in f.rt.journeys.values()] == before
    status = await EngineCustomerClient(engine).journey_status(f.jid)
    assert not PII & set(status)
    await f.http.aclose()


def test_build_client_defaults_to_local_http():
    import httpx

    http = httpx.AsyncClient(base_url="http://x")
    assert isinstance(build_customer_client({}, http=http), A2ACustomerClient)


@pytest.mark.parametrize(
    "env",
    [
        {"ENGINE_MODE": "bogus"},
        {"ENGINE_MODE": "agent-engine"},
        {"ENGINE_MODE": "agent-engine", "ACME_CUSTOMER_ENGINE": "acme-customer-agent"},
    ],
)
def test_build_client_rejects_bad_config(env):
    with pytest.raises(ValueError):
        build_customer_client(env)


def test_build_client_agent_engine_uses_given_handle():
    handle = engine_over(Flow().rt)
    c = build_customer_client({"ENGINE_MODE": "agent-engine"}, engine=handle)
    assert isinstance(c, EngineCustomerClient)


async def test_sales_engine_app_proposes_through_the_customer_engine():
    f = Flow("engine")
    await f.acknowledged_replan()  # one offer already sent by the flow's own radar
    app = build_sales_engine_app(engine_over(f.rt))
    app.set_up()
    events = [e async for e in app.async_stream_query(message="tick", user_id="u")]
    # A fresh radar re-proposes; the Customer-side guardrail (duplicate rule) backstops it.
    assert events[-1]["content"]["parts"][0]["text"] == "proposed 1 offer(s)"
    assert len(f.offer_messages()) == 1
    assert [o.status for o in f.state.offers] == ["sent", "rejected"]
    assert DISPLAY_NAME == "acme-sales-agent"
    assert app.register_operations() == {
        "": ["diagnostics"],
        "async_stream": ["async_stream_query"],
    }
    await f.http.aclose()


def test_sales_engine_set_up_configures_pii_safe_logging(monkeypatch):
    import acme.logging_setup as logging_setup

    customer = engine_over(Flow().rt)  # its own set_up also configures logging
    calls = []
    monkeypatch.setattr(logging_setup, "configure_logging", lambda: calls.append(1))
    build_sales_engine_app(customer).set_up()
    assert calls == [1]
