import httpx
import pytest

from acme.api.app import create_app
from acme.demo.front import BLOCKED_PREFIXES, create_front


@pytest.fixture
def static(tmp_path):
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>console</html>")
    (root / "assets" / "a.js").write_text("x=1")
    return root


@pytest.fixture
async def client(static):
    upstream = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://upstream"
    )
    front = create_front(static, upstream)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=front), base_url="http://t"
    ) as c:
        yield c
    await upstream.aclose()


async def test_serves_index_and_assets(client):
    assert "console" in (await client.get("/")).text
    assert (await client.get("/assets/a.js")).text == "x=1"


async def test_spa_fallback(client):
    r = await client.get("/journeys/J1")
    assert r.status_code == 200 and "console" in r.text


async def test_api_proxies_to_runtime(client):
    r = await client.get("/api/openapi.json")
    assert r.status_code == 200 and "paths" in r.json()


async def test_scenario_resolves_end_to_end(client):
    r = await client.post(
        "/api/scenario/trigger-cancellation",
        json={"flight": "AC1234", "reason": "maintenance"},
    )
    assert r.status_code == 200
    jid = r.json()["journey_ids"][0]
    assert (await client.get(f"/api/journeys/{jid}")).json()["status"] == "RESOLVED"


@pytest.mark.parametrize(
    "path",
    [
        "/api/a2a",
        "/api/a2a/x",
        "/api/.well-known/agent-card.json",
        "/a2a",
        "/.well-known/agent-card.json",
        "/mcp/pss/",
        "/api/mcp/pss/",
        "/api/A2A",
        "/api/%61%32a",
        "/api//a2a",
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_internal_endpoints_are_404(client, path, method):
    assert (await client.request(method, path)).status_code == 404


def test_blocked_prefixes_documented():
    assert "/api/a2a" in BLOCKED_PREFIXES


async def test_path_traversal_does_not_escape_static(client, static):
    (static.parent / "secret.txt").write_text("secret")
    r = await client.get("/..%2fsecret.txt")
    assert "secret" not in r.text
    r = await client.get("/%2e%2e/secret.txt")
    assert "secret" not in r.text
