"""Replay tape (#279, ADR 0103): what the LLM actually answered during a scenario.

The engine records every answer in order (`RecordingLlm`), returns the tape to its caller with the
scenario state, and the caller sends it back on every call; a rebuild replays it (`TapeLlm`)
instead of re-composing with the fake model. The tape holds passenger text: it goes only between the
Customer engine and its caller, never to logs, `ACME_DIAG`, spans, metrics or Sales. Untrusted
input (`clean_tape`): at most MAX_ENTRIES well-formed entries; a malformed tape is ignored whole.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time
from typing import Any

from acme.llm.fake import FakeLlm
from acme.llm.port import Acknowledge, DepartureAfter, LlmPort, Other, StructuredRequest

MAX_ENTRIES, MAX_TEXT = 32, 2000
Entry = dict[str, Any]


def _encode(result: StructuredRequest) -> Entry:
    if isinstance(result, DepartureAfter):
        return {"m": "parse", "kind": "departure_after", "time": result.time.strftime("%H:%M")}
    if isinstance(result, Acknowledge):
        return {"m": "parse", "kind": "ack"}
    return {"m": "parse", "kind": "other", "text": result.text[:MAX_TEXT]}


def _decode(entry: Entry) -> StructuredRequest:
    if entry["kind"] == "departure_after":
        return DepartureAfter(time.fromisoformat(entry["time"]))
    return Acknowledge() if entry["kind"] == "ack" else Other(text=entry["text"])


def _valid(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("fail") is True:
        return entry.get("m") in ("compose", "parse") and set(entry) == {"m", "fail"}
    if entry.get("m") == "compose":
        text = entry.get("text")
        return set(entry) == {"m", "text"} and isinstance(text, str) and len(text) <= MAX_TEXT
    if entry.get("m") != "parse":
        return False
    kind = entry.get("kind")
    if kind == "ack":
        return set(entry) == {"m", "kind"}
    if kind == "other":
        text = entry.get("text")
        return (
            set(entry) == {"m", "kind", "text"} and isinstance(text, str) and len(text) <= MAX_TEXT
        )
    if kind == "departure_after" and set(entry) == {"m", "kind", "time"}:
        try:
            _decode(entry)
        except (TypeError, ValueError):
            return False
        return True
    return False


def clean_tape(raw: Any) -> list[Entry] | None:
    """The tape if it is well-formed, else None (ignored)."""
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES or not all(_valid(e) for e in raw):
        return None
    return [dict(e) for e in raw]


class RecordingLlm:
    """Wraps the engine's model and records each answer (or failure), in call order."""

    def __init__(self, inner: LlmPort) -> None:
        self.inner, self.entries = inner, []  # type: ignore[var-annotated]

    def reset(self) -> None:
        self.entries = []

    def _room(self) -> bool:
        return len(self.entries) < MAX_ENTRIES  # never grow past what a rebuild would accept

    async def compose_passenger_message(self, context: Mapping[str, Any]) -> str:
        try:
            text = await self.inner.compose_passenger_message(context)
        except Exception:
            if self._room():
                self.entries.append({"m": "compose", "fail": True})
            raise
        if self._room():
            self.entries.append({"m": "compose", "text": text[:MAX_TEXT]})
        return text

    async def parse_passenger_request(self, text: str) -> StructuredRequest:
        try:
            result = await self.inner.parse_passenger_request(text)
        except Exception:
            if self._room():
                self.entries.append({"m": "parse", "fail": True})
            raise
        if self._room():
            self.entries.append(_encode(result))
        return result


class TapeLlm:
    """Replays a cleaned tape, per method and in order. A missing or mismatching entry falls back
    to the fake model and is counted in `misses`; it never fails the call. What it answered is
    in `entries`, the tape the rebuilt engine carries on."""

    def __init__(self, tape: list[Entry]) -> None:
        self._tape = {m: [e for e in tape if e["m"] == m] for m in ("compose", "parse")}
        self._seen = {"compose": 0, "parse": 0}
        self._fake, self.misses, self.entries = FakeLlm(), 0, []  # type: ignore[var-annotated]

    def _next(self, method: str) -> Entry | None:
        i, self._seen[method] = self._seen[method], self._seen[method] + 1
        return self._tape[method][i] if i < len(self._tape[method]) else None

    async def compose_passenger_message(self, context: Mapping[str, Any]) -> str:
        entry = self._next("compose")
        if entry is None:
            self.misses += 1
            entry = {"m": "compose", "text": await self._fake.compose_passenger_message(context)}
        self.entries.append(entry)
        if entry.get("fail"):
            raise RuntimeError("replayed LLM failure")
        return entry["text"]

    async def parse_passenger_request(self, text: str) -> StructuredRequest:
        entry = self._next("parse")
        if entry is None:
            self.misses += 1
            entry = _encode(await self._fake.parse_passenger_request(text))
        self.entries.append(entry)
        if entry.get("fail"):
            raise RuntimeError("replayed LLM failure")
        return _decode(entry)
