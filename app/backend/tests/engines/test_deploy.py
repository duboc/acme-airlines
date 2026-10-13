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
        "ACME_GENAI_LOCATION": "global",  # model location: not derived from GOOGLE_CLOUD_LOCATION
        "ACME_GENAI_PROJECT": "acme-airlines-factory",  # the injected value is the project NUMBER
        "ACME_ENABLE_TRACING": "true",
    }
    assert any(r.startswith("google-cloud-aiplatform") for r in cust["requirements"])
    assert cust["extra_packages"] == ["acme"]
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


def test_bucket_403_fails_fast_pointing_at_the_operator_issue(monkeypatch, capsys):
    import sys
    import types

    from google.api_core.exceptions import Forbidden

    from acme.engines import deploy as mod

    class Boom:
        def list(self, **_):
            raise Forbidden("GET https://storage.googleapis.com/storage/v1/b/stage: 403")

    fake_vertexai = types.SimpleNamespace(init=lambda **_: None, agent_engines=Boom())
    monkeypatch.setitem(sys.modules, "vertexai", fake_vertexai)
    monkeypatch.setitem(sys.modules, "vertexai.agent_engines", fake_vertexai.agent_engines)
    for k, v in {
        **ENV,
        "AGENT_ENGINE_LOCATION": "us-central1",
        "AGENT_ENGINE_STAGING_BUCKET": "stage",
    }.items():
        monkeypatch.setenv(k, v)
    assert mod.main() == 1
    assert "#186" in capsys.readouterr().err


def test_deploy_env_has_no_reserved_google_cloud_keys():
    """Agent Engine reserves and injects GOOGLE_CLOUD_* (a 400 otherwise, #190)."""
    sdk = FakeSdk()
    deploy(sdk, ENV)
    for _, kwargs in sdk.calls:
        keys = set(kwargs["env_vars"])
        assert not [k for k in keys if k.startswith("GOOGLE_CLOUD_")], keys
        assert kwargs["env_vars"]["ACME_GENAI_LOCATION"] == "global"
        assert kwargs["env_vars"]["LLM_MODE"] == "vertex"


def test_deploy_does_not_need_google_cloud_project_in_the_environment():
    deploy(FakeSdk(), {"AGENT_ENGINE_SA": ENV["AGENT_ENGINE_SA"]})


class DiagSdk(FakeSdk):
    """FakeSdk whose engines answer `diagnostics()`."""

    def __init__(self, diag_by_resource, **kw):
        super().__init__(**kw)
        self.diag_by_resource = diag_by_resource

    def get(self, resource_name):
        d = self.diag_by_resource[resource_name]
        if isinstance(d, Exception):
            return SimpleNamespace(diagnostics=lambda: (_ for _ in ()).throw(d))
        return SimpleNamespace(diagnostics=lambda: d)


READY = {"status": "ready", "startup_error": None}


def test_a_failed_startup_fails_the_deploy_and_prints_the_error():
    from acme.engines.deploy import verify_started

    sdk = DiagSdk(
        {
            PREFIX + "1": READY,
            PREFIX + "2": {"status": "failed", "startup_error": "Traceback: boom"},
        }
    )
    with pytest.raises(DeployError, match="(?s)acme-sales-agent.*boom"):
        verify_started(sdk, {"acme-customer-agent": PREFIX + "1", "acme-sales-agent": PREFIX + "2"})


def test_ready_engines_pass_verification():
    from acme.engines.deploy import verify_started

    sdk = DiagSdk({PREFIX + "1": READY, PREFIX + "2": READY})
    verify_started(sdk, {"acme-customer-agent": PREFIX + "1", "acme-sales-agent": PREFIX + "2"})


def test_an_unreachable_diagnostics_call_is_a_deploy_failure_too():
    from acme.engines.deploy import verify_started

    sdk = DiagSdk({PREFIX + "1": RuntimeError("503 unavailable")})
    with pytest.raises(DeployError, match="diagnostics call failed.*503"):
        verify_started(sdk, {"acme-customer-agent": PREFIX + "1"})


def test_main_exits_nonzero_and_prints_the_startup_error(monkeypatch, capsys):
    import sys
    import types

    from acme.engines import deploy as mod

    class Sdk(DiagSdk):
        pass

    sdk = Sdk({})
    sdk.diag_by_resource = {}
    created = {}

    def create(**kw):
        name = PREFIX + f"new{len(created)}"
        created[name] = kw
        sdk.diag_by_resource[name] = {"status": "failed", "startup_error": "ModuleNotFoundError: x"}
        return SimpleNamespace(resource_name=name)

    sdk.create = create
    fake = types.SimpleNamespace(init=lambda **_: None, agent_engines=sdk)
    monkeypatch.setitem(sys.modules, "vertexai", fake)
    monkeypatch.setitem(sys.modules, "vertexai.agent_engines", sdk)
    for k, v in {
        **ENV,
        "AGENT_ENGINE_LOCATION": "us-central1",
        "AGENT_ENGINE_STAGING_BUCKET": "stage",
    }.items():
        monkeypatch.setenv(k, v)
    assert mod.main() == 1
    assert "ModuleNotFoundError: x" in capsys.readouterr().err
