"""The narrow LLM interface agents depend on."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Protocol


@dataclass(frozen=True)
class DepartureAfter:
    time: time


@dataclass(frozen=True)
class Acknowledge:
    """Positive or neutral confirmation, e.g. "Perfect, thanks!"."""


@dataclass(frozen=True)
class Other:
    text: str
    # A model Acknowledge that the deterministic veto downgraded (#421, #470): for telemetry only,
    # so it never takes part in equality (`Other(t, vetoed=True) == Other(t)`).
    vetoed: bool = field(default=False, compare=False)


StructuredRequest = DepartureAfter | Acknowledge | Other


class LlmPort(Protocol):
    async def compose_passenger_message(self, context: Mapping[str, Any]) -> str: ...

    async def parse_passenger_request(self, text: str) -> StructuredRequest: ...
