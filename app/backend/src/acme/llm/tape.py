"""Replay tape (#279, ADR 0103): what the LLM actually answered during a scenario.

The engine records every answer in order (`RecordingLlm`), returns the tape to its caller with the
scenario state, and the caller sends it back on every call; a rebuild replays it (`TapeLlm`)
instead of re-composing with the fake model. The tape holds passenger text: it goes only between the
Customer engine and its caller, never to logs, `ACME_DIAG`, spans, metrics or Sales. Untrusted
input (`clean_tape`): at most MAX_ENTRIES well-formed entries; a malformed tape is ignored whole.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from datetime import time
from typing import Any

from acme.llm.fake import FakeLlm
from acme.llm.port import Acknowledge, DepartureAfter, LlmPort, Other, StructuredRequest

MAX_ENTRIES, MAX_TEXT = 32, 2000
Entry = dict[str, Any]
KEY = "k"  # optional: what the entry answers; entries without it (older tapes) replay by position


def text_key(text: str) -> str:
    """A short digest of the passenger text: lets the replay tell which call an entry belongs to
    without the tape (or a log) revealing more than it already holds."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def context_key(context: Mapping[str, Any]) -> str:
    """What a compose is for: the Sales offer or the resolution notice. Never content."""
    return "offer" if "offer" in context else "notice"


def _encode(result: StructuredRequest, key: str | None = None) -> Entry:
    extra = {} if key is None else {KEY: key}
    if isinstance(result, DepartureAfter):
        kind = {"kind": "departure_after", "time": result.time.strftime("%H:%M")}
    elif isinstance(result, Acknowledge):
        kind = {"kind": "ack"}
    else:
        kind = {"kind": "other", "text": result.text[:MAX_TEXT]}
    return {"m": "parse", **kind, **extra}


def _decode(entry: Entry) -> StructuredRequest:
    if entry["kind"] == "departure_after":
        return DepartureAfter(time.fromisoformat(entry["time"]))
    return Acknowledge() if entry["kind"] == "ack" else Other(text=entry["text"])


def _valid_step(step: Any) -> bool:
    return (
        not isinstance(step, bool)
        and isinstance(step, (int, float))
        and math.isfinite(step)
        and int(step) == step
        and 0 <= int(step) <= MAX_ENTRIES
    )


def manual_entry(op: str, passenger_id: str, value: str, step: int) -> Entry:
    if op == "whatsapp_reply":
        return {
            "m": "manual_reply",
            "pid": passenger_id,
            "text": value[:MAX_TEXT],
            "step": int(step),
        }
    return {"m": "manual_action", "pid": passenger_id, "action": value, "step": int(step)}


def manual_turns(tape: list[Entry] | None) -> list[Entry]:
    if not tape:
        return []
    return [
        {**e, "step": int(e["step"])}
        for e in tape
        if e.get("m") in ("manual_reply", "manual_action")
    ]


def _valid(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("m") == "manual_reply":
        pid, text, step = entry.get("pid"), entry.get("text"), entry.get("step")
        return (
            set(entry) == {"m", "pid", "text", "step"}
            and isinstance(pid, str)
            and 0 < len(pid) <= 64
            and isinstance(text, str)
            and len(text) <= MAX_TEXT
            and _valid_step(step)
        )
    if entry.get("m") == "manual_action":
        pid, action, step = entry.get("pid"), entry.get("action"), entry.get("step")
        return (
            set(entry) == {"m", "pid", "action", "step"}
            and isinstance(pid, str)
            and 0 < len(pid) <= 64
            and action in ("accept_offer", "decline_offer")
            and _valid_step(step)
        )
    if KEY in entry:
        key = entry[KEY]
        if not isinstance(key, str) or not 0 < len(key) <= 32:
            return False
        entry = {k: v for k, v in entry.items() if k != KEY}
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
    return [{**e, "step": int(e["step"])} if "step" in e else dict(e) for e in raw]


class RecordingLlm:
    """Wraps the engine's model and records each answer (or failure), in call order."""

    def __init__(self, inner: LlmPort) -> None:
        self.inner, self.entries = inner, []  # type: ignore[var-annotated]

    def reset(self) -> None:
        self.entries = []

    def _room(self) -> bool:
        return len(self.entries) < MAX_ENTRIES  # never grow past what a rebuild would accept

    def record_manual(self, op: str, passenger_id: str, value: str, step: int) -> None:
        if self._room():
            self.entries.append(manual_entry(op, passenger_id, value, step))

    async def compose_passenger_message(self, context: Mapping[str, Any]) -> str:
        try:
            text = await self.inner.compose_passenger_message(context)
        except Exception:
            if self._room():
                self.entries.append({"m": "compose", "fail": True, KEY: context_key(context)})
            raise
        if self._room():
            self.entries.append(
                {"m": "compose", "text": text[:MAX_TEXT], KEY: context_key(context)}
            )
        return text

    async def parse_passenger_request(self, text: str) -> StructuredRequest:
        try:
            result = await self.inner.parse_passenger_request(text)
        except Exception:
            if self._room():
                self.entries.append({"m": "parse", "fail": True, KEY: text_key(text)})
            raise
        if self._room():
            self.entries.append(_encode(result, text_key(text)))
        return result


class TapeLlm:
    """Replays a cleaned tape, per method. An entry answers a call only if it was recorded for the
    same input (the next unused entry whose key matches; entries without a key match by position).
    A missing or mismatching entry falls back to the fake model and is counted in `misses`; it never
    fails the call. What it answered is in `entries`, the tape the rebuilt engine carries on."""

    def __init__(self, tape: list[Entry]) -> None:
        self._tape = {m: [e for e in tape if e["m"] == m] for m in ("compose", "parse")}
        self._fake, self.misses, self.entries = FakeLlm(), 0, []  # type: ignore[var-annotated]

    def record_manual(self, op: str, passenger_id: str, value: str, step: int) -> None:
        if len(self.entries) < MAX_ENTRIES:
            self.entries.append(manual_entry(op, passenger_id, value, step))

    def _next(self, method: str, key: str) -> Entry | None:
        for i, entry in enumerate(self._tape[method]):
            if entry.get(KEY, key) == key:
                return self._tape[method].pop(i)
        return None

    async def compose_passenger_message(self, context: Mapping[str, Any]) -> str:
        key = context_key(context)
        entry = self._next("compose", key)
        if entry is None:
            self.misses += 1
            text = await self._fake.compose_passenger_message(context)
            entry = {"m": "compose", "text": text, KEY: key}
        self.entries.append(entry)
        if entry.get("fail"):
            raise RuntimeError("replayed LLM failure")
        return entry["text"]

    async def parse_passenger_request(self, text: str) -> StructuredRequest:
        key = text_key(text)
        entry = self._next("parse", key)
        if entry is None:
            self.misses += 1
            entry = _encode(await self._fake.parse_passenger_request(text), key)
        self.entries.append(entry)
        if entry.get("fail"):
            raise RuntimeError("replayed LLM failure")
        return _decode(entry)
