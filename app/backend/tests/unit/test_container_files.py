"""Static checks of the container files (docker is not required): non-root, no secrets, compose
isolation of the Sales agent, offline-by-default LLM, global-only Vertex, no API keys (#24)."""

import re
from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parents[3]
DOCKERFILES = [APP / "backend/Dockerfile", APP / "frontend/Dockerfile"]
COMPOSE = yaml.safe_load((APP / "compose.yaml").read_text())
SERVICES = COMPOSE["services"]


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.parent.name)
def test_final_stage_runs_as_a_non_root_user(path):
    text = path.read_text()
    final = re.split(r"(?m)^FROM ", text)[-1]
    users = re.findall(r"(?m)^USER\s+(\S+)", final)
    if users:
        assert users[-1] not in {"root", "0"}
    else:  # the unprivileged nginx image runs as uid 101 and listens on 8080
        assert final.startswith("nginxinc/nginx-unprivileged")


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.parent.name)
def test_dockerfiles_copy_no_secrets_and_set_none(path):
    text = path.read_text()
    assert not re.search(r"(?im)^(COPY|ADD)\b.*(\.env|\.pem|\.key|credential|secret)", text)
    assert not re.search(r"(?im)^(ENV|ARG)\s+\S*(KEY|TOKEN|SECRET|PASSWORD)", text)


@pytest.mark.parametrize("name", ["backend", "frontend"])
def test_dockerignore_excludes_env_files_and_keys(name):
    ignore = (APP / name / ".dockerignore").read_text().split()
    assert {".env", ".env.*", "*.pem", "*.key"} <= set(ignore)


def test_compose_has_the_five_services_and_no_published_internal_ports():
    assert set(SERVICES) == {"customer-runtime", "acme-sim", "sales-agent", "frontend"}
    published = {n: s.get("ports") for n, s in SERVICES.items() if s.get("ports")}
    assert list(published) == ["frontend"]
    assert all(p.startswith("127.0.0.1:") for p in published["frontend"])


def test_sales_agent_holds_only_the_sales_credential_and_cannot_reach_the_web_network():
    sales = SERVICES["sales-agent"]
    env = sales["environment"]
    assert "ACME_SALES_TOKEN" in env and "ACME_SIM_TOKENS" not in env
    assert "web-net" not in sales["networks"]
    assert "sim-net" in sales["networks"] and "a2a-net" in sales["networks"]
    nets = COMPOSE["networks"]
    assert nets["sim-net"]["internal"] and nets["a2a-net"]["internal"]


def test_acme_sim_is_unreachable_from_the_browser_network():
    assert SERVICES["acme-sim"]["networks"] == ["sim-net"]
    assert "ACME_SIM_TOKENS" in SERVICES["acme-sim"]["environment"]
    assert "ACME_SIM_TOKENS" not in SERVICES["frontend"].get("environment", {})


def test_tokens_are_required_from_the_environment_never_defaulted():
    text = (APP / "compose.yaml").read_text()
    assert "${ACME_SIM_TOKENS:?" in text and "${ACME_SALES_TOKEN:?" in text
    assert not re.search(
        r"ACME_(SIM_TOKENS|SALES_TOKEN):\s+[a-z]+=?[A-Za-z0-9]{12,}\s*$", text, re.M
    )


def test_llm_is_fake_by_default_and_vertex_is_global_without_api_keys():
    env = SERVICES["customer-runtime"]["environment"]
    assert env["LLM_MODE"] == "${LLM_MODE:-fake}"
    assert env["ACME_GENAI_LOCATION"] == "global"
    assert env["GOOGLE_CLOUD_PROJECT"] == "acme-airlines-factory"
    assert "GOOGLE_API_KEY" not in (APP / "compose.yaml").read_text()


def test_containers_are_hardened():
    for name, svc in SERVICES.items():
        assert svc.get("read_only") is True, name
        assert svc.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name


def test_nginx_never_exposes_the_agent_to_agent_endpoint():
    conf = (APP / "frontend/nginx.conf").read_text()
    assert re.search(r"location \^~ /api/a2a \{ return 404; \}", conf)


def test_deploy_doc_is_global_with_service_to_service_iam_and_no_api_keys():
    doc = (APP.parent / "docs/product/deploy.md").read_text()
    assert "ACME_GENAI_LOCATION=global" in doc and "roles/run.invoker" in doc
    assert "--no-allow-unauthenticated" in doc
    assert "api key" in doc.lower() and "never" in doc.lower()
    assert not re.search(r"[a-z]+-[a-z]+\d+-aiplatform\.googleapis\.com", doc)
