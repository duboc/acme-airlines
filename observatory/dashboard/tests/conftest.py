import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.source import FakeDataSource


def make_client(scenario: str = "ok") -> TestClient:
    return TestClient(create_app(FakeDataSource(scenario), budget_usd=1000.0))


@pytest.fixture
def client() -> TestClient:
    return make_client()


@pytest.fixture
def scenario_client():
    return make_client
