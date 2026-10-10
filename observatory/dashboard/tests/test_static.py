from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.source import FakeDataSource


def test_serves_static_when_dist_exists(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    c = TestClient(create_app(FakeDataSource(), static_dir=tmp_path))
    assert "<h1>hi</h1>" in c.get("/").text
    assert c.get("/api/budget").json()["state"] == "ok"


def test_no_static_when_missing(tmp_path):
    c = TestClient(create_app(FakeDataSource(), static_dir=tmp_path / "nope"))
    assert c.get("/").status_code == 404
