"""#230: a Sales-identified caller reaches `POST /a2a` and no other Customer runtime route."""

import logging

import httpx
import pytest

from acme.api.app import create_a2a_app, create_local_app
from acme.sales.client import A2ACustomerClient

SALES = "sales-secret-token-0123456789"
OTHER = "someone-elses-token-0123456789"
FORBIDDEN = [
    ("POST", "/whatsapp/P-MARIANA/messages", {"text": "hi"}),
    ("GET", "/whatsapp/P-MARIANA/messages", None),
    ("POST", "/whatsapp/P-MARIANA/actions", {"action": "accept_offer"}),
    ("POST", "/scenario/reset", None),
    ("POST", "/scenario/trigger-cancellation", {"flight": "AC1234", "reason": "x"}),
    ("GET", "/journeys", None),
    ("GET", "/journeys/J-0a1b2c3d", None),
    ("GET", "/journeys/J-0a1b2c3d/events", None),
    ("GET", "/scenarios", None),
    ("GET", "/openapi.json", None),
    ("GET", "/.well-known/agent-card.json", None),
    ("GET", "/a2a", None),
    ("POST", "/a2a/", None),
    ("POST", "/nope", None),
]


def client(app, token=SALES):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://customer",
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )


@pytest.fixture(params=["runtime", "a2a-listener"])
def app(request):
    main = create_local_app(sales_token=SALES)
    return main if request.param == "runtime" else create_a2a_app(main.state.runtime, SALES)


@pytest.mark.parametrize(("method", "path", "body"), FORBIDDEN)
async def test_given_a_sales_caller_when_it_hits_any_route_but_a2a_then_403_and_audit(
    app, method, path, body, caplog
):
    with caplog.at_level(logging.WARNING, logger="acme.audit"):
        async with client(app) as http:
            resp = await http.request(method, path, json=body)
    assert resp.status_code == 403
    assert any(
        r.name == "acme.audit" and "sales caller denied" in r.message for r in caplog.records
    )
    assert SALES not in caplog.text


async def test_the_sales_denial_changes_nothing_on_the_runtime():
    main = create_local_app(sales_token=SALES)
    rt = main.state.runtime
    await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    async with client(main) as http:
        assert (await http.post("/scenario/reset")).status_code == 403
    assert rt.journeys  # reset did not run


async def test_given_the_customer_to_sales_a2a_path_when_sales_calls_then_it_still_works(app):
    await app.state.runtime.trigger_cancellation("AC1234", "unscheduled maintenance")
    async with client(app) as http:
        ids = await A2ACustomerClient(http).list_journeys()
    assert len(ids) == 1


@pytest.mark.parametrize("token", [None, OTHER])
async def test_callers_without_the_sales_identity_keep_the_passenger_routes(token):
    async with client(create_local_app(sales_token=SALES), token) as http:
        assert (await http.get("/journeys")).status_code == 200


@pytest.mark.parametrize("token", [None, OTHER])
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_callers_without_the_sales_identity_are_denied_on_a2a(app, token, method, caplog):
    with caplog.at_level(logging.WARNING, logger="acme.audit"):
        async with client(app, token) as http:
            resp = await http.request(method, "/a2a", json={"jsonrpc": "2.0", "id": 1})
    assert resp.status_code == 403 and resp.text == "forbidden"
    assert any(r.name == "acme.audit" and r.event == "sales_route_denied" for r in caplog.records)
    assert SALES not in caplog.text and OTHER not in caplog.text


async def test_a2a_guard_fails_closed_when_token_unset(monkeypatch, caplog):
    monkeypatch.delenv("ACME_SALES_TOKEN", raising=False)  # create_local_app falls back to the env
    main = create_local_app(sales_token=None)
    with caplog.at_level(logging.WARNING, logger="acme.audit"):
        async with client(main, None) as http:
            resp = await http.post("/a2a", json={"jsonrpc": "2.0", "id": 1})
    assert resp.status_code == 403 and resp.text == "forbidden"
    assert any(r.name == "acme.audit" and r.event == "sales_route_denied" for r in caplog.records)
    # no token to present or to leak; a presented bearer cannot authenticate either
    async with client(main, OTHER) as http:
        assert (await http.post("/a2a", json={"jsonrpc": "2.0", "id": 1})).status_code == 403
    listener = create_a2a_app(main.state.runtime, None)
    async with client(listener, None) as http:
        assert (await http.post("/a2a", json={"jsonrpc": "2.0", "id": 1})).status_code == 403


async def test_a2a_guard_allows_passenger_routes_when_token_unset(monkeypatch):
    monkeypatch.delenv("ACME_SALES_TOKEN", raising=False)
    async with client(create_local_app(sales_token=None), None) as http:
        assert (await http.get("/journeys")).status_code == 200


BYPASS_HEADERS = {
    "double space": [(b"authorization", f"Bearer  {SALES}".encode())],
    "tab": [(b"authorization", f"Bearer\t{SALES}".encode())],
    "trailing space": [(b"authorization", f"Bearer {SALES} ".encode())],
    "duplicate, junk first": [
        (b"authorization", b"dummy"),
        (b"authorization", f"Bearer {SALES}".encode()),
    ],
    "duplicate, sales first": [
        (b"authorization", f"Bearer {SALES}".encode()),
        (b"authorization", b"dummy"),
    ],
    "lowercase scheme": [(b"authorization", f"bearer {SALES}".encode())],
    " lowercase scheme + extra spaces": [(b"authorization", f"bearer  {SALES}".encode())],
    "unknown scheme": [(b"authorization", f"Token {SALES}".encode())],
    "basic scheme": [(b"authorization", f"Basic {SALES}".encode())],
    "bare token": [(b"authorization", SALES.encode())],
}


@pytest.mark.parametrize("path", ["/scenario/reset", "/a2a"])
@pytest.mark.parametrize("name", BYPASS_HEADERS)
async def test_given_a_malformed_or_duplicated_sales_header_then_still_403(name, path):
    main = create_local_app(sales_token=SALES)
    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http", "method": "POST", "path": path, "query_string": b"",
        "headers": BYPASS_HEADERS[name], "scheme": "http", "server": ("t", 80),
        "client": ("c", 1), "http_version": "1.1", "root_path": "",
    }  # fmt: skip
    await main(scope, receive, send)
    assert sent[0]["status"] == 403


async def test_a2a_rejects_no_space_bearer_and_accepts_single_mixed_case_authorization_header():
    main = create_local_app(sales_token=SALES)

    async def call(headers):
        sent = []

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        scope = {
            "type": "http", "method": "POST", "path": "/a2a", "query_string": b"",
            "headers": headers, "scheme": "http", "server": ("t", 80),
            "client": ("c", 1), "http_version": "1.1", "root_path": "",
        }  # fmt: skip
        await main(scope, receive, send)
        return sent[0]["status"]

    assert await call([(b"authorization", f"Bearer{SALES}".encode())]) == 403
    assert await call([(b"Authorization", f"Bearer {SALES}".encode())]) != 403


def test_customer_runtime_refuses_to_start_without_the_sales_token(monkeypatch):
    from acme.customer.main import main, require_sales_token

    monkeypatch.delenv("ACME_SALES_TOKEN", raising=False)
    monkeypatch.delenv("ENGINE_MODE", raising=False)
    with pytest.raises(SystemExit, match="ACME_SALES_TOKEN is required"):
        require_sales_token({})
    with pytest.raises(SystemExit, match="ACME_SALES_TOKEN is required"):
        require_sales_token({"ACME_SALES_TOKEN": ""})
    with pytest.raises(SystemExit):
        main()  # fails before binding any listener
    require_sales_token({"ACME_SALES_TOKEN": SALES})
    require_sales_token({"ENGINE_MODE": "agent-engine"})  # no local Sales HTTP caller
