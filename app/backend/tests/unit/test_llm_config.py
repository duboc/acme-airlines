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


def test_vertex_refuses_regional_location():
    with pytest.raises(LlmConfigError, match="global"):
        load_vertex_config({"GOOGLE_CLOUD_LOCATION": "us-central1"})


def test_vertex_accepts_explicit_global():
    assert load_vertex_config({"GOOGLE_CLOUD_LOCATION": "global"}).location == "global"


@pytest.mark.parametrize("name", ["GOOGLE_API_KEY", "GEMINI_API_KEY"])
def test_vertex_refuses_api_keys(name):
    with pytest.raises(LlmConfigError, match="API key"):
        load_vertex_config({name: "x"})


def test_vertex_refuses_other_project():
    with pytest.raises(LlmConfigError, match="project"):
        load_vertex_config({"GOOGLE_CLOUD_PROJECT": "other-project"})


def test_model_is_configurable():
    assert load_vertex_config({"LLM_MODEL": "gemini-x"}).model == "gemini-x"
