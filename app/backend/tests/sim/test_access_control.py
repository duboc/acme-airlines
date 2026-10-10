import logging
from contextlib import asynccontextmanager

import httpx
import pytest

from acme.sim.app import DEFAULT_REGISTRY, create_app
from acme.sim.auth import ALLOWLIST, _allowed, client_config, load_tokens
from tests.sim.conftest import TOKENS, bearer, call

# One cheap, read-only tool per server, to prove a *successful* call end to end.
PROBES = {
    "occ": ("flight_ops.subscribe", {}),
    "pss": ("pnr.get", {"pnr": "MSLV42"}),
    "crm": ("profile.get", {"passenger_id": "P-MARIANA"}),
    "inventory": ("cabin_inventory.get", {"flight": "AC1252", "cabin": "premium_business"}),
    "dcs": ("seatmap.get", {"flight": "AC1240"}),
    "worldtracer": ("bag.get", {"tag": "BAG0001"}),
    "concierge": ("voucher.issue", {"type": "meal", "amount": "20"}),
    "whatsapp": ("message.transcript", {"to": "P-MARIANA"}),
    "payments": (
        "charge",
        {"pnr": "MSLV42", "amount": "1", "currency": "USD", "idempotency_key": "ac"},
    ),
    "loyalty": ("member.get", {"passenger_id": "P-MARIANA"}),
}


@asynccontextmanager
async def open_raw(sim):
    """Raw HTTP client with the app lifespan running (needed when a request is let through)."""
    app = create_app(sim, tokens=TOKENS)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acme-sim")
    async with app.router.lifespan_context(app), client:
        yield client


@pytest.fixture
def raw(sim):
    """Lifespan-free client: only for requests the middleware refuses (401/403)."""
    app = create_app(sim, tokens=TOKENS)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acme-sim")


async def post(raw, path, headers=None):
    return await raw.post(path, headers=headers or {}, json={})


@pytest.mark.parametrize("server", sorted(set(DEFAULT_REGISTRY) - {"inventory"}))
async def test_sales_is_refused_on_every_registered_server_except_inventory(raw, server):
    r = await post(raw, f"/mcp/{server}/", bearer("sales"))
    assert r.status_code == 403


async def test_sales_refused_on_unmounted_and_non_mcp_paths(raw):
    for path in ["/mcp/doesnotexist/", "/mcp/doesnotexist", "/", "/admin", "/mcp", "/mcp/"]:
        assert (await post(raw, path, bearer("sales"))).status_code == 403, path


@pytest.mark.parametrize(
    "path",
    [
        "/mcp/inventory/../pss/",
        "/mcp/pss/../inventory/",
        "/mcp/inventory%2F..%2Fpss/",
        "//mcp/pss/",
        "/mcp//pss/",
        "/MCP/pss/",
        "/mcp/inventoryx/",
        "/mcp/inventory.evil/",
        "/mcp/pss/inventory/",
        "/mcp",
        "/mcp/",
        "/",
    ],
)
def test_sales_path_check_is_exact(path):
    assert not _allowed("sales", path)


@pytest.mark.parametrize("path", ["/mcp/inventory/", "/mcp/inventory", "/mcp/inventory/x"])
def test_sales_inventory_paths_allowed(path):
    assert _allowed("sales", path)


async def test_sales_reaches_inventory_over_mcp(connect):
    async with connect("inventory", headers=bearer("sales")) as s:
        inv = await call(s, "cabin_inventory.get", flight="AC1252", cabin="premium_business")
    assert inv["open_seats"] == 4


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"}]
)
@pytest.mark.parametrize("server", sorted(DEFAULT_REGISTRY))
async def test_missing_or_unknown_token_is_401(raw, server, headers):
    assert (await post(raw, f"/mcp/{server}/", headers)).status_code == 401


async def test_401_precedes_403_for_unmounted_paths(raw):
    assert (await post(raw, "/mcp/doesnotexist/")).status_code == 401


@pytest.mark.parametrize("caller", ["customer", "test-admin"])
@pytest.mark.parametrize("server", sorted(DEFAULT_REGISTRY))
async def test_customer_and_admin_reach_every_server(connect, caller, server):
    tool, args = PROBES[server]
    async with connect(server, headers=bearer(caller)) as s:
        res = await s.call_tool(tool, args)
    assert not res.isError, res.content


def test_probes_cover_the_whole_registry():
    assert set(PROBES) >= set(DEFAULT_REGISTRY)


def test_allowlist_shape():
    assert ALLOWLIST["sales"] == frozenset({"inventory"})
    assert ALLOWLIST["customer"] == "*" and ALLOWLIST["test-admin"] == "*"


async def test_unmounted_path_denied_even_for_customer_is_404_not_open(raw):
    # customer may use every *mounted* server; an unmounted one is simply not found
    r = await post(raw, "/mcp/doesnotexist/", bearer("customer"))
    assert r.status_code in (403, 404)


async def test_no_token_values_in_logs_or_error_bodies(sim, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    bodies = []
    async with open_raw(sim) as raw:
        for headers in [bearer("sales"), {"Authorization": "Bearer attacker-secret-token"}, {}]:
            for path in ["/mcp/pss/", "/mcp/inventory/"]:
                bodies.append((await post(raw, path, headers)).text)
    out = capsys.readouterr()
    haystack = caplog.text + out.out + out.err + " ".join(bodies)
    for token in [*TOKENS.values(), "attacker-secret-token"]:
        assert token not in haystack


def test_load_tokens_parses_env_and_validates():
    t = load_tokens({"ACME_SIM_TOKENS": "customer=aaaaaaaaaaaa,sales=bbbbbbbbbbbb"})
    assert t == {"customer": "aaaaaaaaaaaa", "sales": "bbbbbbbbbbbb"}
    with pytest.raises(ValueError):
        load_tokens({"ACME_SIM_TOKENS": "hacker=abcdefghijkl"})
    with pytest.raises(ValueError):
        load_tokens({"ACME_SIM_TOKENS": "customer=short"})
    with pytest.raises(ValueError):
        load_tokens({"ACME_SIM_TOKENS": "customer=samesamesame,sales=samesamesame"})
    with pytest.raises(ValueError):
        load_tokens({"ACME_SIM_TOKENS": "garbage"})


def test_load_tokens_generates_distinct_random_when_unset():
    a, b = load_tokens({}), load_tokens({})
    assert set(a) == {"customer", "sales", "test-admin"}
    assert len(set(a.values())) == 3 and a != b


def test_client_config_per_caller():
    cfg = client_config("sales", "http://sim:8700", TOKENS, servers=list(DEFAULT_REGISTRY))
    assert set(cfg) == {"inventory"}
    assert cfg["inventory"]["url"] == "http://sim:8700/mcp/inventory/"
    assert cfg["inventory"]["headers"] == bearer("sales")
    full = client_config("customer", "http://sim:8700/", TOKENS, servers=list(DEFAULT_REGISTRY))
    assert set(full) == set(DEFAULT_REGISTRY)
    with pytest.raises(KeyError):
        client_config("nobody", "http://x", TOKENS, servers=[])
