import pytest

ENDPOINTS = ["/api/spend", "/api/models", "/api/agents", "/api/github", "/api/budget"]


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/healthz").json() == {"status": "ok"}
    assert client.get("/api/overview").json()["state"] == "ok"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_envelope_on_every_endpoint(client, path):
    body = client.get(path).json()
    assert body["state"] == "ok"
    assert body["as_of"]
    assert body["message"] is None


def test_spend_shape(client):
    body = client.get("/api/spend?days=30").json()
    assert body["currency"] == "USD"
    day = body["series"][0]
    assert set(day) == {"date", "vm", "cloud_run", "vertex_ai", "other", "total"}
    assert day["total"] == pytest.approx(
        day["vm"] + day["cloud_run"] + day["vertex_ai"] + day["other"]
    )
    assert body["totals"]["total"] == pytest.approx(sum(d["total"] for d in body["series"]))
    assert [d["date"] for d in body["series"]] == sorted(d["date"] for d in body["series"])


def test_spend_days_window_and_validation(client):
    assert len(client.get("/api/spend?days=1").json()["series"]) <= 1
    assert client.get("/api/spend?days=0").status_code == 422
    assert client.get("/api/spend?days=400").status_code == 422


def test_models_shape(client):
    from dashboard.service import model_class

    body = client.get("/api/models?hours=24").json()
    assert body["hours"] == 24
    by_model = {m["model"]: m for m in body["models"]}
    assert {"opus", "sonnet", "gemini_flash"} <= set(by_model)
    assert set(by_model["opus"]) == {"model", "label", "tokens", "requests"}
    assert by_model["opus"]["label"] == "Opus"
    assert model_class("gemini-4-argon") == "gemini_pro"
    assert model_class("gemini-3.8-flash") == "gemini_flash"


def test_agents_shape(client):
    body = client.get("/api/agents").json()
    assert body["lead_last_activity"] == "2026-10-10T11:58:00Z"
    assert body["limits_exceeded_count"] == 1
    agents = {a["name"]: a for a in body["agents"]}
    assert agents["acme-backend"]["running_seconds"] == 5400
    assert agents["acme-qa"]["running_seconds"] is None
    assert agents["acme-reviewer"]["limits_exceeded"] is True


def test_agents_latest_snapshot_per_agent(client):
    names = [a["name"] for a in client.get("/api/agents").json()["agents"]]
    assert len(names) == len(set(names))


def test_github_shape(client):
    body = client.get("/api/github").json()
    snap = body["snapshot"]
    assert snap["merges_24h"] == 6
    assert snap["ci_pass_rate"] == pytest.approx(14 / 16)
    assert snap["open_prs"][0]["number"] == 114
    assert body["merges_per_day"][0] == {"date": "2026-10-04", "count": 3}


def test_budget_shape(client):
    body = client.get("/api/budget").json()
    assert body["budget_usd"] == 1000.0
    assert body["mtd_usd"] == pytest.approx(body["percent"] * 10, abs=0.1)
    assert [m["percent"] for m in body["markers"]] == [50, 90, 100]
    assert body["markers"][0] == {"percent": 50, "usd": 500.0, "reached": body["mtd_usd"] >= 500}
    first = body["guard_actions"][0]
    assert set(first) == {"ts", "cost_usd", "budget_usd", "action"}
    assert first["ts"] >= body["guard_actions"][-1]["ts"]


def test_budget_uses_latest_guard_action_budget():
    from dashboard.service import budget
    from dashboard.source import FakeDataSource

    class RaisedBudget(FakeDataSource):
        def guard_logs(self, limit):
            return [
                {
                    "ts": "2026-10-10T11:55:00Z",
                    "text": "cost=1160.64 budget=2000.00 -> no action",
                }
            ]

    res = budget(RaisedBudget("ok"), 1000.0)
    assert res.budget_usd == 2000.0
    assert res.markers[0].usd == 1000.0
    assert res.markers[2].usd == 2000.0


@pytest.mark.parametrize("path", ENDPOINTS)
def test_empty_scenario(scenario_client, path):
    body = scenario_client("empty").get(path).json()
    assert body["state"] == "empty"
    assert body["message"]


@pytest.mark.parametrize("path", ["/api/spend", "/api/agents", "/api/github", "/api/budget"])
def test_lagging_scenario_still_returns_data(scenario_client, path):
    body = scenario_client("lagging").get(path).json()
    assert body["state"] == "lagging"
    assert body["message"]


def test_lagging_keeps_data(scenario_client):
    assert scenario_client("lagging").get("/api/agents").json()["agents"]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_unavailable_scenario(scenario_client, path):
    c = scenario_client("unavailable")
    r = c.get(path)
    assert r.status_code == 200
    assert r.json()["state"] == "unavailable"
    assert r.json()["message"]


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_read_only(client, path, method):
    assert getattr(client, method)(path).status_code == 405


def test_unavailable_has_empty_payload(scenario_client):
    body = scenario_client("unavailable").get("/api/spend").json()
    assert body["series"] == [] and body["totals"]["total"] == 0
    assert scenario_client("unavailable").get("/api/github").json()["snapshot"] is None


def test_collector_health_ok(client):
    body = client.get("/api/collector-health").json()
    assert body["state"] == "ok" and body["healthy"] is True
    assert body["as_of"] == "2026-10-10T12:00:00Z"
    assert body["snapshot_age_seconds"] == 30
    assert body["last_execution"]["status"] == "succeeded"


def test_collector_health_red_when_snapshots_older_than_15_minutes(scenario_client):
    body = scenario_client("lagging").get("/api/collector-health").json()
    assert body["state"] == "lagging" and body["healthy"] is False
    assert "collector" in body["message"].lower()


def test_collector_health_never_ran_is_red_not_silent(scenario_client):
    body = scenario_client("empty").get("/api/collector-health").json()
    assert body["state"] == "empty" and body["healthy"] is False
    assert body["last_execution"] is None
    assert "never" in body["message"].lower()


def test_collector_health_unavailable_is_red(scenario_client):
    body = scenario_client("unavailable").get("/api/collector-health").json()
    assert body["state"] == "unavailable" and body["healthy"] is False


def test_collector_health_failed_execution_is_named(client, monkeypatch):
    from dashboard import app as app_module  # noqa: F401
    from dashboard.app import create_app
    from dashboard.source import FakeDataSource

    class Failing(FakeDataSource):
        def collector_runs(self, limit):
            return [{"ts": "2026-10-10T12:00:00+00:00", "status": "failed",
                     "detail": "Container called exit(2)."}]  # fmt: skip

    from fastapi.testclient import TestClient

    body = TestClient(create_app(Failing("lagging"))).get("/api/collector-health").json()
    assert body["last_execution"]["status"] == "failed"
    assert body["healthy"] is False and "failed" in body["message"]
