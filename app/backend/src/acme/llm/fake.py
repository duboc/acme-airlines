"""Deterministic, offline LLM used for dev, tests and CI."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import time
from typing import Any

from acme.llm.port import Acknowledge, DepartureAfter, Other, StructuredRequest

_AFTER = re.compile(r"\bafter\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)
_ACK = re.compile(r"\b(thanks|thank you|perfect|great|ok(ay)?|got it|sounds good)\b", re.IGNORECASE)
# Fail closed: any negation, complaint or instruction-like wording is never an acknowledgement.
_NOT_ACK = re.compile(
    r"\b(no|not|nope|never|nothing|none|neither|nor|but|stuck|still|worse|terrible|awful|"
    r"bad|ignore|instructions?|prompt|system|output|acknowledge|pretend|override|cannot|accept|"
    r"offer|decline|reject|refuse|cancel|refund|unacceptable|wrong|problem)\b|n't|n\u2019t",
    re.IGNORECASE,
)
_MAX_ACK_WORDS = 8


def can_be_acknowledgement(text: str) -> bool:
    """The deterministic veto on ANY acknowledgement, whoever decided it (the offline model, the
    deployed model or the pattern): never a question, never long, never a negation, complaint,
    request or instruction-like wording (fail closed, #421)."""
    return not (len(text.split()) > _MAX_ACK_WORDS or "?" in text or _NOT_ACK.search(text))


def is_acknowledgement(text: str) -> bool:
    """True only for a short, clearly positive reply with no negation (fail closed)."""
    return can_be_acknowledgement(text) and bool(_ACK.search(text))


def departure_after(text: str) -> DepartureAfter | None:
    """The deterministic reading of "leave after <time>", or None. Used by the offline model and as
    the fallback when the deployed model answers Other for a text this pattern understands."""
    if m := _AFTER.search(text):
        hour, minute, meridiem = int(m[1]), int(m[2] or 0), (m[3] or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if hour < 24 and minute < 60:
            return DepartureAfter(time=time(hour, minute))
    return None


class FakeLlm:
    def __init__(self, parse_script: Mapping[str, StructuredRequest] | None = None) -> None:
        self._script = dict(parse_script or {})

    async def compose_passenger_message(self, context: Mapping[str, Any]) -> str:
        name = context.get("passenger", "there")
        summary = context.get("summary", "")
        return f"Hi {name}, {summary}".strip()

    async def parse_passenger_request(self, text: str) -> StructuredRequest:
        if text in self._script:
            return self._script[text]
        if (after := departure_after(text)) is not None:
            return after
        if is_acknowledgement(text):
            return Acknowledge()
        return Other(text=text)
