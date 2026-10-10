"""Simulated partner systems (2b): Concierge, WhatsApp Business, Payments.

All state lives in `PartnerState`, reachable as `SimWorld.partners`, so API/UI and tests can
inspect it. Catalogue: docs/product/simulators.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from pydantic import AwareDatetime, Field, StringConstraints

from acme.sim.servers import _server
from acme.sim.world import SimError

if TYPE_CHECKING:
    from acme.sim.world import SimWorld

PositiveMoney = Annotated[Decimal, Field(gt=0, le=10000, decimal_places=2)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


def _ref(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8].upper()}"


class PartnerState:
    def __init__(self, sim: SimWorld) -> None:
        self._sim = sim
        self.hotel_bookings: dict[str, dict[str, Any]] = {}
        self.transfers: dict[str, dict[str, Any]] = {}
        self.vouchers: dict[str, dict[str, Any]] = {}
        self.charges: dict[str, dict[str, Any]] = {}  # by idempotency key
        self.transcripts: dict[str, list[dict[str, Any]]] = {}
        self.inbound: list[dict[str, Any]] = []
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._cond = asyncio.Condition()

    def _now(self) -> str:
        return self._sim.clock.now().isoformat()

    # --- concierge ---
    def book_hotel(self, hotel_id: str, nights: int, pax: int, passenger_id: str | None) -> dict:
        if nights < 1 or pax < 1:
            raise SimError("nights and pax must be >= 1")
        if hotel_id not in self._sim.world.hotels_with_rooms:
            raise SimError(f"hotel {hotel_id} has no rooms")
        ref = _ref("HB")
        rec = {"booking_ref": ref, "hotel_id": hotel_id, "nights": nights, "pax": pax,
               "passenger_id": passenger_id, "at": self._now()}  # fmt: skip
        self.hotel_bookings[ref] = rec
        return rec

    def book_transfer(self, origin: str, destination: str, at: AwareDatetime) -> dict:
        ref = _ref("TR")
        rec = {"transfer_ref": ref, "origin": origin, "destination": destination,
               "pickup_at": at.isoformat()}  # fmt: skip
        self.transfers[ref] = rec
        return rec

    def issue_voucher(self, type: str, amount: Any) -> dict:
        vid = _ref("VC")
        rec = {"voucher_id": vid, "type": type, "amount": f"{amount:.2f}", "currency": "USD"}
        self.vouchers[vid] = rec
        return rec

    # --- whatsapp ---
    def _log(self, passenger_id: str, direction: str, body: str, buttons: list[str]) -> dict:
        msg = {"message_id": _ref("WM"), "passenger_id": passenger_id, "direction": direction,
               "body": body, "buttons": buttons, "at": self._now()}  # fmt: skip
        self.transcripts.setdefault(passenger_id, []).append(msg)
        return msg

    def send(self, to: str, body: str, buttons: list[str] | None) -> dict:
        return self._log(to, "outbound", body, list(buttons or []))

    def add_inbound_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    async def receive(self, sender: str, body: str) -> dict:
        async with self._cond:
            msg = self._log(sender, "inbound", body, [])
            self.inbound.append(msg)
            self._cond.notify_all()
        for listener in self._listeners:
            listener(msg)
        return msg

    async def inbound_after(self, cursor: int, timeout_s: float) -> tuple[list[dict], int]:
        async with self._cond:
            if len(self.inbound) <= cursor and timeout_s > 0:
                try:
                    await asyncio.wait_for(
                        self._cond.wait_for(lambda: len(self.inbound) > cursor), timeout_s
                    )
                except TimeoutError:
                    pass
            return list(self.inbound[cursor:]), len(self.inbound)

    # --- payments ---
    def charge(self, pnr: str, amount: Any, currency: str, key: str) -> dict:
        self._sim.get_pnr(pnr)
        request = {"pnr": pnr, "amount": f"{amount:.2f}", "currency": currency}
        if key in self.charges:
            prior = self.charges[key]
            if {k: prior[k] for k in request} != request:
                raise SimError(f"idempotency key {key!r} was used with different parameters")
            return {**prior, "idempotent_replay": True}
        rec = {**request, "charge_id": _ref("CH"), "status": "succeeded", "at": self._now()}
        self.charges[key] = rec
        return {**rec, "idempotent_replay": False}


def build_concierge(sim: SimWorld) -> FastMCP:
    mcp = _server("concierge")
    p = sim.partners

    @mcp.tool(name="hotel.book")
    def hotel_book(
        hotel_id: str, nights: int, pax: int, passenger_id: str | None = None
    ) -> dict[str, Any]:
        """Book partner hotel rooms (HT8842 has rooms)."""
        return p.book_hotel(hotel_id, nights, pax, passenger_id)

    @mcp.tool(name="transfer.book")
    def transfer_book(origin: str, destination: str, at: AwareDatetime) -> dict[str, Any]:
        """Book a ground transfer (`from`/`to` in the brief are `origin`/`destination`)."""
        return p.book_transfer(origin, destination, at)

    @mcp.tool(name="voucher.issue")
    def voucher_issue(
        type: Literal["meal", "hotel", "transport"], amount: PositiveMoney
    ) -> dict[str, Any]:
        """Issue a digital voucher (USD)."""
        return p.issue_voucher(type, amount)

    return mcp


def build_whatsapp(sim: SimWorld) -> FastMCP:
    mcp = _server("whatsapp")
    p = sim.partners

    @mcp.tool(name="message.send")
    def message_send(to: str, body: str, buttons: list[str] | None = None) -> dict[str, Any]:
        """Send a WhatsApp message to a passenger, optionally with quick-reply buttons."""
        return p.send(to, body, buttons)

    @mcp.tool(name="message.transcript")
    def message_transcript(to: str) -> dict[str, Any]:
        """Full transcript (both directions) with timestamps and buttons."""
        return {"messages": list(p.transcripts.get(to, []))}

    @mcp.tool(name="message.receive")
    async def message_receive(sender: str, body: str) -> dict[str, Any]:
        """Inbound hook: a passenger message arrives (used by the API/UI and tests)."""
        return await p.receive(sender, body)

    @mcp.tool(name="message.inbound")
    async def message_inbound(cursor: int = 0, timeout_s: float = 0) -> dict[str, Any]:
        """Long-poll inbound passenger messages with index >= cursor (for the Customer runtime)."""
        msgs, nxt = await p.inbound_after(cursor, min(timeout_s, 60))
        return {"messages": msgs, "next_cursor": nxt}

    return mcp


def build_payments(sim: SimWorld) -> FastMCP:
    mcp = _server("payments")

    @mcp.tool(name="charge")
    def charge(
        pnr: str, amount: PositiveMoney, currency: Currency, idempotency_key: str
    ) -> dict[str, Any]:
        """Charge a PNR. Idempotent per `idempotency_key`."""
        return sim.partners.charge(pnr, amount, currency, idempotency_key)

    return mcp
