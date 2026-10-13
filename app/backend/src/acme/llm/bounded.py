"""A bounded wait for every model call (#231).

A slow Gemini call is cancelled after `timeout_s`. Composing then raises `LlmTimeout`, which the
callers already treat like any LLM failure (deterministic template, fallback metric). Parsing
fails CLOSED: the passenger's text becomes `Other`, so nothing is ever acknowledged (no offer)
and the request goes to a person.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from opentelemetry import trace

from acme import metrics
from acme.llm.port import LlmPort, Other, StructuredRequest

log = logging.getLogger(__name__)
DEFAULT_TIMEOUT_S = 8.0


class LlmTimeout(TimeoutError):
    """The model call exceeded its bound and was cancelled."""


def load_timeout(env: Mapping[str, str]) -> float:
    raw = env.get("LLM_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def _record(op: str, timeout_s: float) -> None:
    log.warning("llm_timeout op=%s timeout_s=%s fallback=true", op, timeout_s)  # no payload
    span = trace.get_current_span()
    span.set_attribute("acme.llm.timeout", True)
    span.set_attribute("acme.llm.op", op)


class BoundedLlm:
    def __init__(self, inner: LlmPort, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._inner, self._timeout = inner, timeout_s

    async def compose_passenger_message(self, context: Mapping[str, Any]) -> str:
        try:
            return await asyncio.wait_for(
                self._inner.compose_passenger_message(context), self._timeout
            )
        except TimeoutError:
            _record("compose", self._timeout)  # the caller counts its own template fallback
            raise LlmTimeout("compose timed out") from None

    async def parse_passenger_request(self, text: str) -> StructuredRequest:
        try:
            return await asyncio.wait_for(self._inner.parse_passenger_request(text), self._timeout)
        except TimeoutError:
            _record("parse", self._timeout)
            metrics.template_fallback("parse_timeout")
            return Other(text=text)
