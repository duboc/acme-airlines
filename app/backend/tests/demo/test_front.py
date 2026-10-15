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


async def test_a_vanished_browser_closes_the_upstream_stream_within_a_few_seconds(
    static, monkeypatch
):
    """#305: waiting for the next chunk of an idle SSE stream hid that the client was gone, so the
    upstream (and the engine behind it) kept being polled. The proxy now looks every second."""
    import asyncio

    from acme.demo import front

    monkeypatch.setattr(front, "DISCONNECT_POLL_SECONDS", 0.05)
    closed = asyncio.Event()

    class Idle(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"retry: 3000\n\n"
            await asyncio.sleep(3600)  # an idle stream: no event, no keep-alive in this window

        async def aclose(self):
            closed.set()

    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Idle()
            )
        ),
        base_url="http://upstream",
    )
    app = create_front(static, upstream)
    sent, gone = [], asyncio.Event()

    requested = []

    async def receive():
        if not requested:  # the (empty) request body first, then silence until the browser leaves
            requested.append(True)
            return {"type": "http.request", "body": b"", "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            gone.set()  # the browser leaves right after the first bytes

    scope = {"type": "http", "method": "GET", "path": "/api/journeys/J/events", "headers": [],
             "query_string": b"", "scheme": "http", "server": ("t", 80)}  # fmt: skip
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    assert closed.is_set()  # the upstream response was closed: the engine stops being polled
    await upstream.aclose()
