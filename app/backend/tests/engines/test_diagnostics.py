"""Self-diagnosing engines (#185): a broken set_up() must be readable, never silent or leaky."""

import re
import tomllib
from pathlib import Path

import pytest

from acme.diagnostics import MAX_ERROR_CHARS, scrub
from acme.engines import customer, sales
from acme.engines.customer import EngineOperationError, build_customer_engine_app

LOCK = Path(__file__).resolve().parents[2] / "uv.lock"


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")


def broken_customer(monkeypatch, exc=None):
    from acme.customer import runtime

    def boom(*_a, **_k):
        raise exc or RuntimeError("boom in CustomerRuntime")

    monkeypatch.setattr(runtime, "CustomerRuntime", boom)
    app = build_customer_engine_app()
    app.set_up()  # must NOT raise
    return app


def test_healthy_engine_reports_ready():
    app = build_customer_engine_app()
    app.set_up()
    from acme.telemetry import process_identity

    assert app.diagnostics() == {
        "engine": "acme-customer-agent", "status": "ready", "startup_error": None,
        "llm_mode": "fake", "identity": process_identity(), "last_rebuild": None,
    }  # fmt: skip


def test_set_up_failure_is_captured_not_raised(monkeypatch):
    app = broken_customer(monkeypatch)
    d = app.diagnostics()
    assert d["status"] == "failed" and "RuntimeError: boom in CustomerRuntime" in d["startup_error"]
    assert "set_up" in d["startup_error"]  # frames are kept


async def test_other_operations_fail_with_a_clear_error_after_a_failed_start(monkeypatch):
    app = broken_customer(monkeypatch)
    with pytest.raises(EngineOperationError, match="failed to start"):
        app.journeys_list()
    with pytest.raises(EngineOperationError, match="failed to start"):
        await app.a2a_skill("list_journeys", {})


def test_unstarted_engine_is_reported_as_such():
    app = build_customer_engine_app()
    assert app.diagnostics()["status"] == "starting"
    with pytest.raises(EngineOperationError, match="not started"):
        app.journeys_list()


def test_diagnostics_is_a_registered_operation_only_on_the_customer_engine():
    assert "diagnostics" in build_customer_engine_app().register_operations()[""]
    assert "diagnostics" in sales.build_sales_engine_app().register_operations()[""]


async def test_diagnostics_is_not_an_a2a_skill_so_sales_cannot_reach_it():
    app = build_customer_engine_app()
    app.set_up()
    reply = await app.a2a_skill("diagnostics", {})
    assert "error" in reply  # unknown skill: only the three PII-free skills are served
    from acme.customer.skills import SKILLS

    assert "diagnostics" not in SKILLS
    from acme.sales.client import EngineCustomerClient

    assert [n for n in dir(EngineCustomerClient) if "diagnos" in n] == []


def test_diagnostics_payload_has_exactly_six_fields_and_no_environment(monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "tok-1234567890-abcdef")
    app = broken_customer(monkeypatch, RuntimeError("bad token tok-1234567890-abcdef"))
    d = app.diagnostics()
    assert set(d) == {"engine", "status", "startup_error", "llm_mode", "identity",
                 "last_rebuild"}  # fmt: skip
    assert "tok-1234567890-abcdef" not in d["startup_error"]


# --- scrubbing -----------------------------------------------------------------------------
def test_scrub_masks_env_secrets_by_name(monkeypatch):
    monkeypatch.setenv("ACME_SALES_TOKEN", "s3cr3t-value-123")
    monkeypatch.setenv("MY_PASSWORD", "hunter2hunter2")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_abcdefghijklmnop")
    monkeypatch.setenv("PLAIN_VAR", "visible-value")
    out = scrub("a s3cr3t-value-123 b hunter2hunter2 c ghp_abcdefghijklmnop d visible-value")
    assert "s3cr3t" not in out and "hunter2" not in out and "ghp_" not in out
    assert "visible-value" in out


@pytest.mark.parametrize(
    "secret",
    [
        "Bearer abc.def.ghi-123456",
        "ya29.A0ARrdaM-abcdefghijklmnopqrstuvwxyz",
        "AIzaSyA-1234567890abcdefghijklmnopqrstu",
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----",
        "x" * 60,
    ],
)
def test_scrub_masks_credential_shapes(secret):
    out = scrub(f"failed with {secret} while calling")
    assert secret not in out and "[redacted]" in out


def test_scrub_masks_passenger_pii():
    out = scrub("Mariana Silva MSLV42 mariana@example.com +55 11 91234-5678")
    for pii in ("Mariana", "MSLV42", "@example.com", "91234"):
        assert pii not in out


def test_scrub_truncates_and_keeps_ordinary_text():
    assert scrub("ModuleNotFoundError: No module named 'acme'") == (
        "ModuleNotFoundError: No module named 'acme'"
    )
    assert len(scrub("a b " * 5000)) <= MAX_ERROR_CHARS + 40


def test_error_text_has_no_local_variable_values(monkeypatch):
    def deep():
        password_value = "local-secret-xyz"  # noqa: F841
        raise ValueError("plain failure")

    from acme.diagnostics import format_error

    try:
        deep()
    except ValueError as exc:
        text = format_error(exc)
    assert "local-secret-xyz" not in text and "ValueError: plain failure" in text


# --- engine requirements are pinned to uv.lock ----------------------------------------------
def test_engine_requirements_are_exact_pins_matching_uv_lock():
    locked = {p["name"]: p["version"] for p in tomllib.loads(LOCK.read_text())["package"]}
    pinned = [r for r in customer.engine_requirements() if r.startswith(("google", "opentel"))]
    assert pinned
    for req in customer.engine_requirements():
        m = re.fullmatch(r"([A-Za-z0-9_.-]+)(\[[^\]]+\])?==([\w.]+)", req)
        assert m, f"{req} is not an exact pin"
        assert locked[m.group(1).lower()] == m.group(3), f"{req} drifted from uv.lock"


@pytest.mark.parametrize(
    ("factory", "expected_engine"),
    [
        (build_customer_engine_app, "acme-customer-agent"),
        (sales.build_sales_engine_app, "acme-sales-agent"),
    ],
)
def test_given_pickled_fake_mode_app_when_set_up_runs_in_vertex_env_then_diagnostics_reports_vertex(
    monkeypatch, factory, expected_engine
):
    """#440 AC1: Given an engine app constructed under LLM_MODE=fake and pickled/unpickled with
    cloudpickle into a container where LLM_MODE=vertex and ACME_GENAI_PROJECT=acme-airlines-factory,
    when set_up() runs, _set_up() refreshes self.llm_mode so diagnostics()['llm_mode'] == 'vertex'.
    """
    import cloudpickle
    from google import genai

    from acme.sales import client as sales_client

    monkeypatch.setenv("LLM_MODE", "fake")
    built_on_ci = factory()
    assert built_on_ci.diagnostics()["llm_mode"] == "fake"
    blob = cloudpickle.dumps(built_on_ci)

    monkeypatch.setenv("LLM_MODE", "vertex")
    monkeypatch.setenv("ACME_GENAI_PROJECT", "acme-airlines-factory")
    monkeypatch.setenv("ACME_GENAI_LOCATION", "global")
    monkeypatch.setenv(
        "ACME_CUSTOMER_ENGINE",
        "projects/acme-airlines-factory/locations/us-central1/reasoningEngines/111",
    )
    monkeypatch.setattr(genai, "Client", lambda **_kw: object())
    monkeypatch.setattr(sales_client, "_AdcEngineHandle", lambda _res: object())

    unpickled = cloudpickle.loads(blob)
    assert unpickled.diagnostics()["llm_mode"] == "fake"  # before set_up(): pickled value
    unpickled.set_up()
    diag = unpickled.diagnostics()
    assert diag["engine"] == expected_engine
    assert diag["status"] == "ready" and diag["startup_error"] is None
    assert diag["llm_mode"] == "vertex"


@pytest.mark.parametrize("factory", [build_customer_engine_app, sales.build_sales_engine_app])
@pytest.mark.parametrize(
    ("bad_env", "expected_substr"),
    [
        (
            {
                "LLM_MODE": "vertex",
                "ACME_GENAI_PROJECT": "acme-airlines-factory",
                "ACME_GENAI_LOCATION": "us-central1",
            },
            "ACME_GENAI_LOCATION must be 'global'",
        ),
        (
            {"LLM_MODE": "vertex"},
            "GOOGLE_CLOUD_PROJECT",
        ),
    ],
)
def test_given_pickled_app_when_set_up_runs_with_invalid_vertex_env_then_diagnostics_reports_failed(
    monkeypatch, factory, bad_env, expected_substr
):
    """#440 AC2: Given an engine app unpickled into a container where LLM_MODE=vertex has an
    invalid setting, when set_up() runs, validate_env() raises EngineConfigError and diagnostics()
    reports status == 'failed' with startup_error."""
    import cloudpickle

    monkeypatch.setenv("LLM_MODE", "fake")
    blob = cloudpickle.dumps(factory())

    for key in ("ACME_GENAI_PROJECT", "GOOGLE_CLOUD_PROJECT", "ACME_GENAI_LOCATION"):
        monkeypatch.delenv(key, raising=False)
    for key, val in bad_env.items():
        monkeypatch.setenv(key, val)

    unpickled = cloudpickle.loads(blob)
    unpickled.set_up()
    diag = unpickled.diagnostics()
    assert diag["status"] == "failed"
    assert "EngineConfigError" in diag["startup_error"]
    assert expected_substr in diag["startup_error"]
