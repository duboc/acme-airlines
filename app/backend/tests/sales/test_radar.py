import ast
from pathlib import Path

import httpx
import pytest

from acme.api.app import create_app
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.sales.client import A2ACustomerClient
from acme.sales.config import SALES_SERVERS, sales_mcp_config
from acme.sales.radar import SalesRadar
from acme.sim.auth import load_tokens
from acme.sim.client import SimClient, inprocess_connector

SALES_DIR = Path(__file__).resolve().parents[2] / "src/acme/sales"


@pytest.fixture
async def world():
    app = create_app(FixedClock(DISRUPTION_DETECTED_AT))
    rt = app.state.runtime
    rt.auto_duty_of_care = False
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    await rt.sim.rebook("MSLV42", "AC1252")
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://customer")
    inventory = SimClient(inprocess_connector(rt.sim, "sales"))
    radar = SalesRadar(A2ACustomerClient(http), inventory)
    yield radar, rt, jid
    await http.aclose()


def resolve(rt, jid, acknowledged=True):
    state = rt.journeys[jid].state
    state.transition(S.RESOLVED)
    state.resolution_acknowledged = acknowledged
    return state


def proposed(state):
    return [r for r in state.offers if r.status == "proposed"]


async def test_resolving_journey_sends_nothing_and_records_suppression(world):
    radar, rt, jid = world
    assert await radar.tick() == []
    state = rt.journeys[jid].state
    assert state.offers == []
    assert any(e.type == "sales_suppressed" for e in state.timeline)


async def test_resolved_acknowledged_sends_exactly_one_structured_offer(world):
    radar, rt, jid = world
    state = resolve(rt, jid)
    sent = await radar.tick()
    assert len(sent) == 1
    (rec,) = proposed(state)
    o = rec.offer
    assert (str(o.price), str(o.standard_price), str(o.net_margin)) == ("135.00", "290.00", "80.00")
    assert (o.flight, o.cabin, o.optional, o.origin, o.journey_id) == (
        "AC1252", "premium_business", True, "sales", jid,
    )  # fmt: skip


async def test_five_ticks_send_one_proposal_total(world):
    radar, rt, jid = world
    state = resolve(rt, jid)
    for _ in range(5):
        await radar.tick()
    assert len(state.offers) == 1


async def test_unacknowledged_resolved_sends_nothing(world):
    radar, rt, jid = world
    state = resolve(rt, jid, acknowledged=False)
    assert await radar.tick() == [] and state.offers == []


async def test_no_open_premium_seats_sends_nothing(world):
    radar, rt, jid = world
    state = resolve(rt, jid)
    rt.world.inventory[0].open_seats = 0
    assert await radar.tick() == [] and state.offers == []


async def test_flight_without_inventory_sends_nothing(world):
    radar, rt, jid = world
    state = resolve(rt, jid)
    await rt.sim.rebook("MSLV42", "AC1260")  # no premium inventory seeded for AC1260
    assert await radar.tick() == [] and state.offers == []


async def test_new_flight_after_replanning_allows_one_more_proposal(world):
    radar, rt, jid = world
    state = resolve(rt, jid)
    await radar.tick()
    assert len(state.offers) == 1
    state.transition(S.REPLANNING)
    state.transition(S.RESOLVED)
    state.resolution_acknowledged = True
    await (
        radar.tick()
    )  # same (journey, flight): the sent-set holds even if the offer was invalidated
    assert len(state.offers) == 1


def test_sales_tool_config_is_inventory_only():
    cfg = sales_mcp_config("http://sim:8700", load_tokens({}))
    assert SALES_SERVERS == ("inventory",)
    assert set(cfg) == {"inventory"}
    assert cfg["inventory"]["url"] == "http://sim:8700/mcp/inventory/"


async def test_sales_inventory_tool_list_is_only_inventory_tools(world):
    _, rt, _ = world
    connect = inprocess_connector(rt.sim, "sales")
    async with connect("inventory") as s:
        tools = {t.name for t in (await s.list_tools()).tools}
    assert tools == {"cabin_inventory.get", "upgrade_fare.get"}


async def test_sales_credential_cannot_reach_other_servers(world):
    from acme.sim.client import ToolError

    _, rt, _ = world
    client = SimClient(inprocess_connector(rt.sim, "sales"))
    for server, tool, args in [
        ("whatsapp", "message.send", {"to": "P-MARIANA", "body": "buy now"}),
        ("payments", "charge", {}),
        ("crm", "profile.get", {"passenger_id": "P-MARIANA"}),
        ("pss", "pnr.get", {"pnr": "MSLV42"}),
        ("dcs", "seatmap.get", {"flight": "AC1252"}),
    ]:
        with pytest.raises(ToolError, match="not allowed"):
            await client.call(server, tool, **args)
    assert rt.sim.partners.transcripts == {}


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


FORBIDDEN = (
    "acme.customer", "acme.api", "acme.sim.world", "acme.sim.partners", "acme.sim.dcs",
    "acme.sim.servers", "acme.sim.loyalty", "acme.runtime",
)  # fmt: skip


@pytest.mark.parametrize("path", sorted(SALES_DIR.glob("*.py")), ids=lambda p: p.name)
def test_sales_package_imports_no_customer_or_passenger_channel_code(path):
    bad = [i for i in _imports(path) if i.startswith(FORBIDDEN)]
    assert bad == []


def test_sales_package_has_no_passenger_channel_strings():
    text = "\n".join(p.read_text() for p in SALES_DIR.glob("*.py"))
    for word in ("whatsapp", "message.send", "payments", "profile.get"):
        assert word not in text.lower().replace("passenger-channel", "")
