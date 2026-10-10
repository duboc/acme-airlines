import json
import logging

import httpx
import pytest

from collector.hub import HubError, fetch_agents, hub_source, map_agent, resolve_project
from collector.schema import TABLES, validate_row
from collector.sources import FIXTURES

TOKEN = "s3cret-token-value"
TABLE = next(t for t in TABLES if t.name == "agent_snapshots")
RAW = json.loads((FIXTURES / "hub_agents.json").read_text())
TS = "2026-10-10T12:00:00+00:00"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://hub")


def ok_handler(request):
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.url.path == "/api/v1/projects/00000000-0000-0000-0000-0000000000aa/agents"
    return httpx.Response(200, json=RAW)


def test_fixture_maps_to_valid_rows_one_per_agent():
    rows = hub_source(
        client(ok_handler), "00000000-0000-0000-0000-0000000000aa", TOKEN, sleep=lambda s: None
    )(TS)
    assert len(rows) == len(RAW["agents"])
    for row in rows:
        validate_row(TABLE, row)
        assert row["ts"] == TS


def by_name(rows):
    return {r["name"]: r for r in rows}


def test_tier_role_limits_and_lead_activity():
    rows = by_name([map_agent(a, TS) for a in RAW["agents"]])
    assert rows["acme-backend"]["tier"] == "standing"
    assert rows["acme-backend"]["role"] == "backend"
    assert rows["acme-obs-backend"]["tier"] == "ephemeral"
    assert rows["acme-reviewer"]["limits_exceeded"] is True
    assert rows["acme-backend"]["limits_exceeded"] is False
    assert rows["acme-lead"]["role"] == "lead"
    assert rows["acme-lead"]["last_activity"]
    assert rows["acme-em-observatory"]["role"] == "eng-manager"
    assert rows["acme-backend"]["container_status"].startswith("Exited")


def test_missing_tier_label_is_standing():
    row = map_agent({"name": "x", "template": "acme-qa", "labels": {}}, TS)
    assert row["tier"] == "standing"
    assert row["phase"] is None and row["limits_exceeded"] is False


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": "unauthorized"}),
        httpx.Response(503),
        httpx.ConnectTimeout("slow"),
    ],
)
def test_failures_raise_hub_error_and_never_log_token(response, caplog):
    calls = []

    def handler(request):
        calls.append(1)
        if isinstance(response, Exception):
            raise response
        return response

    caplog.set_level(logging.DEBUG)
    with pytest.raises(HubError) as e:
        fetch_agents(client(handler), "proj-1", TOKEN, sleep=lambda s: None)
    assert TOKEN not in str(e.value) and TOKEN not in caplog.text
    # 401 is not retried; 5xx and timeouts are, a bounded number of times
    assert len(calls) == (
        1 if response.__class__ is httpx.Response and response.status_code == 401 else 3
    )


def test_missing_token_or_endpoint_is_an_error(monkeypatch):
    from collector.hub import live_hub_source

    monkeypatch.delenv("SCION_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HUB_ENDPOINT", "http://hub")
    monkeypatch.setenv("HUB_PROJECT", "p")
    with pytest.raises(HubError, match="SCION_HUB_TOKEN"):
        live_hub_source()(TS)


def test_malformed_body_is_hub_error():
    handler = lambda r: httpx.Response(200, json={"nope": 1})  # noqa: E731
    with pytest.raises(HubError):
        fetch_agents(client(handler), "p", TOKEN, sleep=lambda s: None)


UUID = "61b6ae3d-9335-4360-a259-5b306dc229f6"


def test_slug_is_resolved_once_then_used_in_path():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/api/v1/projects":
            assert request.url.params["slug"] == "my-slug"
            return httpx.Response(200, json={"projects": [{"id": UUID, "slug": "my-slug"}]})
        assert request.url.path == f"/api/v1/projects/{UUID}/agents"
        return httpx.Response(200, json=RAW)

    source = hub_source(client(handler), "my-slug", TOKEN, sleep=lambda s: None)
    source(TS)
    source(TS)
    assert seen.count("/api/v1/projects") == 1


def test_uuid_skips_lookup():
    def boom(request):
        raise AssertionError("no lookup expected")

    assert resolve_project(client(boom), UUID, TOKEN) == UUID


@pytest.mark.parametrize("response", [httpx.Response(403), httpx.Response(404)])
def test_slug_lookup_failure_names_env_var(response):
    with pytest.raises(HubError, match="HUB_PROJECT.*UUID"):
        resolve_project(client(lambda r: response), "my-slug", TOKEN)


def test_slug_not_found_in_empty_list():
    h = lambda r: httpx.Response(200, json={"projects": []})  # noqa: E731
    with pytest.raises(HubError, match="HUB_PROJECT.*UUID"):
        resolve_project(client(h), "my-slug", TOKEN)
