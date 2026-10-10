"""Change agent interface. The real implementation is #30; this stub records the request."""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime, JourneyRecord


class ChangeAgent(Protocol):
    async def handle(
        self, rt: CustomerRuntime, rec: JourneyRecord, request_id: str, after: time
    ) -> None: ...


class RecordingChangeAgent:
    """Records departure_after hand-offs and sends an honest holding reply."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, time]] = []

    async def handle(
        self, rt: CustomerRuntime, rec: JourneyRecord, request_id: str, after: time
    ) -> None:
        self.requests.append((rec.state.journey_id, request_id, after))
        rec.state.append_event(
            "customer_agent",
            "change_handoff",
            {"request_id": request_id, "after": after.strftime("%H:%M")},
        )
        await rt.send(
            rec.passenger_id,
            f"Got it. I am looking for flights leaving after {after:%H:%M} and will update you.",
        )
