"""Shared fixtures for the governance suite ("Two Goals, One Guardrail").

The suite attacks each layer on its own: sim access control, schema validation, policy and
state. Nothing here weakens a check; a real violation is filed as a bug and marked
`xfail(strict=True)` with the issue number so the suite stays green until it is fixed.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from acme.api.app import create_app
from acme.customer.skills import CustomerSkills
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, mariana_world
from acme.domain.journey import DisruptionStatus as S
from acme.domain.journey import JourneyState
from acme.domain.models import Offer
from acme.sales.client import A2ACustomerClient, EngineCustomerClient
from acme.sales.radar import SalesRadar
from acme.sim.client import SimClient, inprocess_connector
from tests.governance.flow import engine_over
from tests.sim.conftest import bearer, connect, sim  # noqa: F401  (fixtures re-exported)

INVENTORY = mariana_world().inventory[0]
ROUTE = {
    S.DETECTED: [],
    S.RESOLVING: [S.RESOLVING],
    S.RESOLVED: [S.RESOLVING, S.RESOLVED],
    S.REPLANNING: [S.RESOLVING, S.RESOLVED, S.REPLANNING],
    S.CLOSED: [S.RESOLVING, S.RESOLVED, S.CLOSED],
}
JOURNEY_ID = "J-0a1b2c3d"
__all__ = ["INVENTORY", "ROUTE", "bearer", "connect", "sim"]


def make_offer(**over) -> Offer:
    base = dict(
        offer_id=uuid4(), journey_id=JOURNEY_ID, flight="AC1252", cabin="premium_business",
        price=Decimal("135"), standard_price=Decimal("290"), net_margin=Decimal("80"),
        currency="USD", origin="sales", optional=True,
    )  # fmt: skip
    return Offer(**{**base, **over})


def make_journey(status=S.RESOLVED, ack=True, tier="diamond", flight="AC1252") -> JourneyState:
    j = JourneyState(
        JOURNEY_ID, FixedClock(DISRUPTION_DETECTED_AT), passenger_tier=tier, current_flight=flight
    )
    for step in ROUTE[status]:
        j.transition(step)
    j.resolution_acknowledged = ack
    return j


@pytest.fixture(params=["http", "engine"])
async def stack(request, monkeypatch):
    """Customer app + A2A skills + Sales radar (inventory-only caller) over one seeded world."""
    monkeypatch.setenv("ACME_SALES_TOKEN", "t-sales")  # /a2a fails closed without a token (#575)
    app = create_app(FixedClock(DISRUPTION_DETECTED_AT))
    rt = app.state.runtime
    rt.auto_duty_of_care = False
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    await rt.sim.rebook("MSLV42", "AC1252")
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://customer",
        headers={"Authorization": "Bearer t-sales"},
    )
    customer = (
        EngineCustomerClient(engine_over(rt))
        if request.param == "engine"
        else A2ACustomerClient(http)
    )
    radar = SalesRadar(customer, SimClient(inprocess_connector(rt.sim, "sales")))
    yield type(
        "Stack",
        (),
        {
            "app": app,
            "rt": rt,
            "jid": jid,
            "radar": radar,
            "skills": CustomerSkills(rt),
            "http": http,
        },
    )
    await http.aclose()
