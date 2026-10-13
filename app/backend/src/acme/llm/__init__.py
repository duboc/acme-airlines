"""LLM access layer. Use ``build_llm()``; default is the offline ``FakeLlm``."""

from __future__ import annotations

import os
from collections.abc import Mapping

from acme.llm.bounded import BoundedLlm, load_timeout
from acme.llm.config import load_llm_mode, load_vertex_config
from acme.llm.fake import FakeLlm
from acme.llm.port import Acknowledge, DepartureAfter, LlmPort, Other, StructuredRequest
from acme.llm.vertex import VertexLlm

__all__ = [
    "BoundedLlm",
    "Acknowledge",
    "DepartureAfter",
    "FakeLlm",
    "LlmPort",
    "Other",
    "StructuredRequest",
    "VertexLlm",
    "build_llm",
]


def build_llm(env: Mapping[str, str] | None = None) -> LlmPort:
    env = os.environ if env is None else env
    if load_llm_mode(env) == "vertex":
        return BoundedLlm(VertexLlm(load_vertex_config(env)), load_timeout(env))
    return FakeLlm()
