"""Shared specialist types (kept import-light to avoid cycles with the runtime)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Emit = Callable[[str, dict[str, Any]], None]  # emit(type, payload) writes a timeline entry


@dataclass(frozen=True)
class RebuildResult:
    new_flight: str | None
    seat_done: bool
    bag_done: bool
