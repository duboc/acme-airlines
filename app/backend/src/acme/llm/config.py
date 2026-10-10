"""LLM configuration: fake by default; Vertex AI global endpoint only, ADC only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

PROJECT = "acme-airlines-factory"
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
    project = env.get("GOOGLE_CLOUD_PROJECT", PROJECT)
    if project != PROJECT:
        raise LlmConfigError(f"GOOGLE_CLOUD_PROJECT must be {PROJECT!r}, got {project!r}")
    location = env.get("GOOGLE_CLOUD_LOCATION", LOCATION)
    if location != LOCATION:
        raise LlmConfigError(
            f"GOOGLE_CLOUD_LOCATION must be 'global' (global endpoint only), got {location!r}"
        )
    return VertexConfig(
        project=PROJECT, location=LOCATION, model=env.get("LLM_MODEL") or DEFAULT_MODEL
    )
