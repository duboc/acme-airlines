import pytest

from acme.llm import FakeLlm, build_llm
from acme.llm.config import (
    DEFAULT_MODEL,
    LOCATION,
    PROJECT,
    LlmConfigError,
    load_llm_mode,
    load_vertex_config,
)


def test_mode_defaults_to_fake_and_builds_fake():
    assert load_llm_mode({}) == "fake"
    assert isinstance(build_llm({}), FakeLlm)


def test_bogus_mode_raises():
    with pytest.raises(LlmConfigError):
        build_llm({"LLM_MODE": "bogus"})


def test_vertex_config_defaults_are_global_and_factory_project():
    cfg = load_vertex_config({"LLM_MODE": "vertex"})
    assert (cfg.project, cfg.location, cfg.model) == (PROJECT, LOCATION, DEFAULT_MODEL)
    assert LOCATION == "global"


def test_vertex_refuses_regional_acme_genai_location():
    with pytest.raises(LlmConfigError, match="global"):
        load_vertex_config({"ACME_GENAI_LOCATION": "us-central1"})


def test_vertex_accepts_explicit_global():
    assert load_vertex_config({"ACME_GENAI_LOCATION": "global"}).location == "global"


@pytest.mark.parametrize("injected", ["us-central1", "global", "europe-west4"])
def test_model_location_ignores_the_platform_injected_google_cloud_location(injected):
    """Agent Engine injects GOOGLE_CLOUD_LOCATION (the engine region); never used (#190)."""
    assert load_vertex_config({"GOOGLE_CLOUD_LOCATION": injected}).location == "global"


def test_vertex_client_gets_location_global_explicitly_despite_regional_env(monkeypatch):
    import sys
    import types

    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

    fake = types.ModuleType("google.genai")
    fake.Client = FakeClient
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    monkeypatch.setattr("google.genai", fake, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    from acme.llm.vertex import VertexLlm

    VertexLlm(load_vertex_config(dict(__import__("os").environ)))
    assert seen == {"vertexai": True, "project": PROJECT, "location": "global"}


def test_project_comes_from_acme_genai_project_then_injected_google_cloud_project():
    assert load_vertex_config({"ACME_GENAI_PROJECT": PROJECT}).project == PROJECT
    assert load_vertex_config({"GOOGLE_CLOUD_PROJECT": PROJECT}).project == PROJECT
    with pytest.raises(LlmConfigError, match="project"):
        load_vertex_config({"ACME_GENAI_PROJECT": "other", "GOOGLE_CLOUD_PROJECT": PROJECT})


@pytest.mark.parametrize("name", ["GOOGLE_API_KEY", "GEMINI_API_KEY"])
def test_vertex_refuses_api_keys(name):
    with pytest.raises(LlmConfigError, match="API key"):
        load_vertex_config({name: "x"})


def test_vertex_refuses_other_project():
    with pytest.raises(LlmConfigError, match="project"):
        load_vertex_config({"GOOGLE_CLOUD_PROJECT": "other-project"})


def test_model_is_configurable():
    assert load_vertex_config({"LLM_MODEL": "gemini-x"}).model == "gemini-x"


def test_project_number_of_the_same_project_is_accepted_and_normalised():
    """Agent Engine injects GOOGLE_CLOUD_PROJECT as the project NUMBER (#185)."""
    cfg = load_vertex_config({"GOOGLE_CLOUD_PROJECT": "493084803309"})
    assert cfg.project == PROJECT  # the client always gets the project id
    assert load_vertex_config({"ACME_GENAI_PROJECT": "493084803309"}).project == PROJECT


@pytest.mark.parametrize("other", ["111111111111", "493084803310", "other-project", "4930848033"])
def test_other_projects_and_numbers_are_refused(other):
    with pytest.raises(LlmConfigError, match="project"):
        load_vertex_config({"GOOGLE_CLOUD_PROJECT": other})
    with pytest.raises(LlmConfigError, match="project"):
        load_vertex_config({"ACME_GENAI_PROJECT": other})


def test_acme_genai_project_wins_over_the_injected_number():
    cfg = load_vertex_config(
        {"ACME_GENAI_PROJECT": PROJECT, "GOOGLE_CLOUD_PROJECT": "493084803309"}
    )
    assert cfg.project == PROJECT
    with pytest.raises(LlmConfigError, match="project"):
        load_vertex_config({"ACME_GENAI_PROJECT": "other", "GOOGLE_CLOUD_PROJECT": "493084803309"})
