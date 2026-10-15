from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from tests.sim.conftest import call


async def test_crm_profile_and_interaction_log(connect, sim):
    async with connect("crm") as s:
        prof = await call(s, "profile.get", passenger_id="P-MARIANA")
        logged = await call(s, "interaction.log", passenger_id="P-MARIANA",
                            channel="whatsapp", summary="Rebooked to AC1240")  # fmt: skip
    assert prof["name"] == "Mariana Silva" and prof["tier"] == "diamond"
    assert logged["interaction_id"]
    assert sim.interactions[0]["summary"] == "Rebooked to AC1240"


async def test_inventory_is_pii_free(connect):
    async with connect("inventory") as s:
        inv = await call(s, "cabin_inventory.get", flight="AC1252", cabin="premium_business")
        fare = await call(s, "upgrade_fare.get", flight="AC1252", cabin="premium_business")
        tools = {t.name for t in (await s.list_tools()).tools}
    assert inv == {"flight": "AC1252", "cabin": "premium_business", "open_seats": 4}
    assert fare["standard_fare"] == "290.00" and fare["cost_floor"] == "55.00"
    assert tools == {"cabin_inventory.get", "upgrade_fare.get"}


async def test_inventory_unknown_is_error(connect):
    async with connect("inventory") as s:
        res = await s.call_tool(
            "cabin_inventory.get", {"flight": "AC1240", "cabin": "premium_business"}
        )
    assert res.isError


async def test_registry_is_pluggable(sim):
    from mcp.server.fastmcp import FastMCP

    from acme.sim.app import DEFAULT_REGISTRY, create_app

    assert {"occ", "pss", "crm", "inventory"} <= set(DEFAULT_REGISTRY)
    extra = FastMCP("extra", streamable_http_path="/")
    app = create_app(sim, registry={"extra": lambda _sim: extra}, tokens={})
    assert [r.path for r in app.routes] == ["/mcp/extra"]


# --- #354: inventory and DCS describe one world ---------------------------------------------


def _world():
    from acme.sim.dcs import dcs_state
    from acme.sim.world import SimWorld

    sim = SimWorld.seeded(FixedClock(DISRUPTION_DETECTED_AT))
    return sim, dcs_state(sim)


def test_inventory_follows_the_seats_dcs_holds_assigns_and_releases():
    sim, dcs = _world()
    seats = sim.flight("AC1252").open_seats["premium_business"]
    assert sim.cabin_inventory("AC1252", "premium_business").open_seats == len(seats) == 4
    dcs.hold("AC1252", seats[0], "MSLV42")
    assert (
        sim.cabin_inventory("AC1252", "premium_business").open_seats == 3
    )  # a held seat is not free
    dcs.release("AC1252", seats[0], "MSLV42")
    assert sim.cabin_inventory("AC1252", "premium_business").open_seats == 4
    dcs.assign("AC1252", seats[0], "MSLV42", "premium_business")
    assert sim.cabin_inventory("AC1252", "premium_business").open_seats == 3  # now it is taken


def test_inventory_is_zero_when_no_seat_is_open_and_never_above_the_seed_count():
    sim, _ = _world()
    inv = next(i for i in sim.world.inventory if i.flight_number == "AC1252")
    inv.open_seats = 2  # a test (or the seed) that says fewer than the seat map
    assert sim.cabin_inventory("AC1252", "premium_business").open_seats == 2
    inv.open_seats = 0
    assert sim.cabin_inventory("AC1252", "premium_business").open_seats == 0
    inv.open_seats = 4
    sim.flight("AC1252").open_seats["premium_business"].clear()
    assert sim.cabin_inventory("AC1252", "premium_business").open_seats == 0


def test_a_cabin_the_flight_has_no_seat_map_for_keeps_the_seed_count():
    sim, _ = _world()
    sim.flight("AC1252").open_seats.pop("premium_business")
    assert sim.cabin_inventory("AC1252", "premium_business").open_seats == 4
