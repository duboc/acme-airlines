"""Deploy script (#163) with a fake SDK, plus workflow assertions. Never touches GCP."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from acme.engines.deploy import DeployError, deploy

ENV = {
    "AGENT_ENGINE_SA": "acme-agent-engine@acme-airlines-factory.iam.gserviceaccount.com",
    "GOOGLE_CLOUD_PROJECT": "acme-airlines-factory",
}
PREFIX = "projects/1/locations/us-central1/reasoningEngines/"
WORKFLOW = Path(__file__).resolve().parents[4] / ".github/workflows/agent-engine-deploy.yml"


class FakeSdk:
    def __init__(self, existing: dict[str, list[str]] | None = None):
        self.existing = existing or {}
        self.calls: list[tuple[str, dict]] = []

    def list(self, *, filter=""):  # noqa: A002
        name = filter.split('"')[1]
        return [
            SimpleNamespace(resource_name=r, gca_resource=SimpleNamespace(display_name=name))
            for r in self.existing.get(name, [])
        ]

    def create(self, **kw):
        self.calls.append(("create", kw))
        return SimpleNamespace(resource_name=PREFIX + f"new{len(self.calls)}")

    def update(self, resource_name, **kw):
        self.calls.append(("update", {"resource_name": resource_name, **kw}))
        return SimpleNamespace(resource_name=resource_name)


def test_updates_existing_in_place_with_the_exact_settings():
    sdk = FakeSdk({"acme-customer-agent": [PREFIX + "111"], "acme-sales-agent": [PREFIX + "222"]})
    out = deploy(sdk, ENV)
    assert out == {"acme-customer-agent": PREFIX + "111", "acme-sales-agent": PREFIX + "222"}
    assert [c[0] for c in sdk.calls] == ["update", "update"]
    cust, sales = (c[1] for c in sdk.calls)
    assert cust["resource_name"] == PREFIX + "111"
    assert cust["service_account"] == ENV["AGENT_ENGINE_SA"]
    assert (cust["min_instances"], cust["max_instances"]) == (1, 1)
    assert cust["env_vars"] == {
        "LLM_MODE": "vertex",
        "GOOGLE_CLOUD_PROJECT": "acme-airlines-factory",
        "GOOGLE_CLOUD_LOCATION": "global",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
    }
    assert any(r.startswith("google-cloud-aiplatform") for r in cust["requirements"])
    assert cust["extra_packages"] == ["src/acme"]
    assert sales["env_vars"]["ENGINE_MODE"] == "agent-engine"
    assert sales["env_vars"]["ACME_CUSTOMER_ENGINE"] == PREFIX + "111"
    assert "min_instances" not in sales


def test_creates_both_when_none_exist_customer_first_and_wires_sales_to_it():
    sdk = FakeSdk()
    out = deploy(sdk, ENV)
    assert [c[0] for c in sdk.calls] == ["create", "create"]
    assert [c[1]["display_name"] for c in sdk.calls] == ["acme-customer-agent", "acme-sales-agent"]
    assert sdk.calls[1][1]["env_vars"]["ACME_CUSTOMER_ENGINE"] == out["acme-customer-agent"]


@pytest.mark.parametrize("dup", ["acme-customer-agent", "acme-sales-agent"])
def test_duplicates_fail_before_anything_is_written(dup):
    sdk = FakeSdk({dup: [PREFIX + "1", PREFIX + "2"]})
    with pytest.raises(DeployError, match="refusing"):
        deploy(sdk, ENV)
    assert sdk.calls == []


def test_workflow_has_no_pr_trigger_and_uses_wif_only():
    text = WORKFLOW.read_text()
    wf = yaml.safe_load(text)
    on = wf.get("on", wf.get(True))
    assert set(on) == {"push", "workflow_dispatch"}
    assert on["push"]["branches"] == ["main"] and on["push"]["paths"]
    assert wf["permissions"] == {"contents": "read"}
    job = next(iter(wf["jobs"].values()))
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert wf["concurrency"]["cancel-in-progress"] is False
    auth = next(s for s in job["steps"] if "auth@" in s.get("uses", ""))
    assert "OBSERVATORY_WIF_PROVIDER" in auth["with"]["workload_identity_provider"]
    assert "OBSERVATORY_DEPLOYER_SA" in auth["with"]["service_account"]
    assert "credentials_json" not in auth["with"]
    assert "secrets." not in text and "pull_request" not in text
    assert "--set-env-vars" not in text and "--update-env-vars" in text
    assert "ENGINE_MODE" not in text  # the demo's ENGINE_MODE belongs to #164
