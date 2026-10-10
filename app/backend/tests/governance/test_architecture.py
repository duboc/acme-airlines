"""Layer 1 (architecture): Sales cannot reach anything but inventory, and sees no PII."""

import json
import re
from pathlib import Path

import pytest

from acme.customer.skills import JourneyStatusView
from acme.sales.config import SALES_SERVERS, sales_mcp_config
from acme.sim.app import DEFAULT_REGISTRY
from acme.sim.auth import ALLOWLIST, client_config
from tests.governance.conftest import bearer
from tests.sim.conftest import TOKENS

SALES_SRC = Path(__file__).resolve().parents[2] / "src/acme/sales"
MARIANA_FACTS = ["Mariana", "Silva", "MSLV42", "P-MARIANA", "FFP-0001", "14A", "BAG0001", "diamond"]


def test_sales_tool_inventory_is_the_inventory_server_only():
    cfg = sales_mcp_config("http://sim", TOKENS)
    assert SALES_SERVERS == ("inventory",)
    assert set(cfg) == {"inventory"}


def test_asking_for_every_server_still_yields_only_inventory_for_sales():
    cfg = client_config("sales", "http://sim", TOKENS, servers=sorted(DEFAULT_REGISTRY))
    assert set(cfg) == {"inventory"}
    assert ALLOWLIST["sales"] == frozenset({"inventory"})


async def test_the_inventory_server_exposes_only_read_only_inventory_tools(connect):
    async with connect("inventory", headers=bearer("sales")) as session:
        names = {t.name for t in (await session.list_tools()).tools}
    assert names == {"cabin_inventory.get", "upgrade_fare.get"}


@pytest.mark.parametrize("server", sorted(set(DEFAULT_REGISTRY) - {"inventory"}))
@pytest.mark.parametrize("suffix", ["", "tools/call", "x/../occ/"])
async def test_sales_credential_is_refused_by_every_other_server(server, suffix):
    import httpx

    from acme.sim.app import create_app
    from acme.sim.world import SimWorld

    app = create_app(SimWorld.seeded(), tokens=TOKENS)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sim"
    ) as c:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}
        r = await c.post(f"/mcp/{server}/{suffix}", headers=bearer("sales"), json=body)
    assert r.status_code == 403


async def test_journey_status_exposes_exactly_four_non_pii_fields(stack):
    view = stack.skills.journey_status(stack.jid).model_dump()
    assert set(view) == {"status", "flight", "resolution_acknowledged", "run_id"}
    # run_id is an opaque random nonce (#104): not derived from the PNR, journey id or passenger
    assert re.fullmatch(r"[0-9a-f]{12}", view["run_id"])
    assert stack.jid not in view["run_id"] and "MSLV42" not in view["run_id"]
    assert set(JourneyStatusView.model_json_schema()["properties"]) == set(view)


async def test_journey_status_over_a2a_leaks_no_passenger_fact(stack):
    resp = await stack.http.post(
        "/a2a",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {"messageId": "m", "role": "agent", "parts": [
                {"kind": "data", "data": {"skill": "journey_status",
                                          "args": {"journey_id": stack.jid}}}]}},
        },
    )  # fmt: skip
    wire = json.dumps(resp.json())
    for fact in MARIANA_FACTS:
        assert fact not in wire, fact


def test_sales_code_has_no_passenger_channel():
    text = "".join(p.read_text().lower() for p in SALES_SRC.glob("*.py"))
    for forbidden in ("whatsapp", "message.send", "crm", "loyalty", "payments"):
        assert forbidden not in text.replace("message/send", ""), forbidden
