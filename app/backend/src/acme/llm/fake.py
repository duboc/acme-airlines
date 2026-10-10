"""Deterministic, offline LLM used for dev, tests and CI."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import time
from typing import Any

from acme.llm.port import Acknowledge, DepartureAfter, Other, StructuredRequest

_AFTER = re.compile(r"\bafter\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)
_ACK = re.compile(r"\b(thanks|thank you|perfect|great|ok(ay)?|got it|sounds good)\b", re.IGNORECASE)


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
        if m := _AFTER.search(text):
            hour, minute, meridiem = int(m[1]), int(m[2] or 0), (m[3] or "").lower()
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            if hour < 24 and minute < 60:
                return DepartureAfter(time=time(hour, minute))
        if _ACK.search(text):
            return Acknowledge()
        return Other(text=text)
