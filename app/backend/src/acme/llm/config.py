"""LLM configuration: fake by default; Vertex AI global endpoint only, ADC only.

The model location comes ONLY from `ACME_GENAI_LOCATION` (default `global`, anything else is
refused). `GOOGLE_CLOUD_LOCATION` is never read: on Vertex AI Agent Engine it is reserved and
injected by the platform as the *engine* region (e.g. `us-central1`), which is not the model
location (#190). The project is `ACME_GENAI_PROJECT`, falling back to the platform-injected
`GOOGLE_CLOUD_PROJECT`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

PROJECT = "acme-airlines-factory"
# Agent Engine injects GOOGLE_CLOUD_PROJECT as the project NUMBER (#185): the same project.
PROJECT_NUMBER = "493084803309"
LOCATION = "global"
# GA Gemini Flash model confirmed to work on the global endpoint (see app/README.md).
DEFAULT_MODEL = "gemini-2.5-flash"

_API_KEY_VARS = ("GOOGLE_API_KEY", "GEMINI_API_KEY")


class LlmConfigError(RuntimeError):
    """Refuse to start: unsafe or invalid LLM configuration."""


@dataclass(frozen=True)
class VertexConfig:
    project: str
    location: str
    model: str


def load_llm_mode(env: Mapping[str, str]) -> str:
    mode = env.get("LLM_MODE", "fake") or "fake"
    if mode not in ("fake", "vertex"):
        raise LlmConfigError(f"LLM_MODE must be 'fake' or 'vertex', got {mode!r}")
    return mode


def load_vertex_config(env: Mapping[str, str]) -> VertexConfig:
    for name in _API_KEY_VARS:
        if env.get(name):
            raise LlmConfigError(f"{name} is set: API keys are not allowed, use ADC only")
    project = env.get("ACME_GENAI_PROJECT") or env.get("GOOGLE_CLOUD_PROJECT") or PROJECT
    if project not in (PROJECT, PROJECT_NUMBER):
        raise LlmConfigError(
            f"model project (ACME_GENAI_PROJECT / GOOGLE_CLOUD_PROJECT) must be {PROJECT!r} "
            f"(or its number), got {project!r}"
        )
    location = env.get("ACME_GENAI_LOCATION") or LOCATION
    if location != LOCATION:
        raise LlmConfigError(
            f"ACME_GENAI_LOCATION must be 'global' (global endpoint only), got {location!r}"
        )
    return VertexConfig(
        project=PROJECT, location=LOCATION, model=env.get("LLM_MODEL") or DEFAULT_MODEL
    )
