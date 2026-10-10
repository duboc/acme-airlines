"""Gemini on Vertex AI, global endpoint, ADC. Only used when LLM_MODE=vertex."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import time
from typing import Any

from acme.llm.config import VertexConfig
from acme.llm.port import Acknowledge, DepartureAfter, Other, StructuredRequest

_PARSE_PROMPT = (
    "Classify the airline passenger's message. Reply with JSON only: "
    '{"kind": "departure_after", "time": "HH:MM"} if they want to leave after a time, '
    '{"kind": "acknowledge"} for a positive/neutral confirmation, otherwise {"kind": "other"}.\n'
    "Message: "
)


class VertexLlm:
    def __init__(self, config: VertexConfig) -> None:
        from google import genai

        self._model = config.model
        self._client = genai.Client(vertexai=True, project=config.project, location=config.location)

    async def _generate(self, prompt: str) -> str:
        resp = await self._client.aio.models.generate_content(model=self._model, contents=prompt)
        return resp.text or ""

    async def compose_passenger_message(self, context: Mapping[str, Any]) -> str:
        return (
            await self._generate(
                "Write a short, warm, plain-text WhatsApp message to the passenger using only "
                f"these facts: {json.dumps(context, default=str)}"
            )
        ).strip()

    async def parse_passenger_request(self, text: str) -> StructuredRequest:
        raw = (await self._generate(_PARSE_PROMPT + text)).strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            data = json.loads(raw)
            if data["kind"] == "departure_after":
                return DepartureAfter(time=time.fromisoformat(data["time"]))
            if data["kind"] == "acknowledge":
                return Acknowledge()
        except (ValueError, KeyError, TypeError):
            pass
        return Other(text=text)
