"""Specialist sub-agents of the Customer agent (deterministic; no LLM).

Each specialist has a typed request/response (pydantic) and `handle(request, emit)`, where
`emit(type, payload)` writes a timeline entry. The typed boundary lets it move behind A2A later.
`RebuildResult` and `run_duty_of_care` are exported lazily to avoid an import cycle with the
runtime.
"""

from typing import Any

__all__ = ["RebuildResult", "run_duty_of_care"]


def __getattr__(name: str) -> Any:
    if name == "RebuildResult":
        from acme.customer.specialists.common import RebuildResult

        return RebuildResult
    if name == "run_duty_of_care":
        from acme.customer.specialists.duty import run_duty_of_care

        return run_duty_of_care
    raise AttributeError(name)
