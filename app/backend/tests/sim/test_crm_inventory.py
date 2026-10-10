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
    app = create_app(sim, registry={"extra": lambda _sim: extra})
    assert [r.path for r in app.routes] == ["/mcp/extra"]
