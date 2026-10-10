"""Seeded scenario world: Mariana's AC1234 disruption.

FICTIONAL, SIMULATED schedule. Block times are deliberately short (e.g. 40 min for
AC1252) to make the connection arithmetic tight and demo-friendly; they are not real.
This module is the single source of truth for times and fares: do not duplicate them.
All times are local; GRU is America/Sao_Paulo, SCL is America/Santiago, both UTC-3 on D.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from acme.domain.models import (
    PNR,
    Bag,
    CabinInventory,
    Flight,
    LoyaltyProfile,
    Passenger,
    SeatAssignment,
    Segment,
)

D = date(2026, 10, 9)
GRU = ZoneInfo("America/Sao_Paulo")
SCL = ZoneInfo("America/Santiago")
SCL_INTERNATIONAL_MCT_MINUTES = 90
PARTNER_HOTEL_ID = "HT8842"
DISRUPTION_DETECTED_AT = datetime.combine(D, time(22, 3), GRU)


class World(BaseModel):
    passengers: dict[str, Passenger]
    flights: dict[str, Flight]
    pnrs: dict[str, PNR]
    inventory: list[CabinInventory]
    mct_minutes: int
    hotels_with_rooms: list[str]


def _at(day_offset: int, hhmm: str, tz: ZoneInfo) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(D + timedelta(days=day_offset), time(h, m), tz)


def mariana_world() -> World:
    flights = [
        Flight(
            number="AC1234",
            origin="GRU",
            destination="SCL",
            status="cancelled",
            scheduled_departure=_at(0, "23:30", GRU),
            scheduled_arrival=_at(1, "01:30", SCL),
        ),
        Flight(
            number="AC1240",
            origin="GRU",
            destination="SCL",
            scheduled_departure=_at(1, "08:15", GRU),
            scheduled_arrival=_at(1, "10:15", SCL),
            open_seats={"economy": ["14A"]},
        ),
        Flight(
            number="AC1252",
            origin="GRU",
            destination="SCL",
            scheduled_departure=_at(1, "11:40", GRU),
            scheduled_arrival=_at(1, "12:20", SCL),
            open_seats={"economy": ["14A"], "premium_business": ["2A", "2B", "3A", "3B"]},
        ),
        Flight(
            number="AC1260",
            origin="GRU",
            destination="SCL",
            scheduled_departure=_at(1, "13:10", GRU),
            scheduled_arrival=_at(1, "14:00", SCL),
            open_seats={"economy": ["14A"]},
        ),
        Flight(
            number="AC0800",
            origin="SCL",
            destination="MAD",
            scheduled_departure=_at(1, "14:00", SCL),
        ),
    ]
    mariana = Passenger(
        passenger_id="P-MARIANA",
        name="Mariana Silva",
        tier="diamond",
        loyalty=LoyaltyProfile(member_id="FFP-0001", tier="diamond", points=250000),
    )
    pnr = PNR(
        locator="MSLV42",
        passenger_id=mariana.passenger_id,
        segments=[Segment(flight_number="AC1234"), Segment(flight_number="AC0800")],
        seats=[SeatAssignment(flight_number="AC1234", seat="14A")],
        bags=[Bag(tag="BAG0001")],
    )
    return World(
        passengers={mariana.passenger_id: mariana},
        flights={f.number: f for f in flights},
        pnrs={pnr.locator: pnr},
        inventory=[
            CabinInventory(
                flight_number="AC1252",
                cabin="premium_business",
                open_seats=4,
                standard_upgrade_fare=Decimal("290.00"),
                cost_floor=Decimal("55.00"),
            )
        ],
        mct_minutes=SCL_INTERNATIONAL_MCT_MINUTES,
        hotels_with_rooms=[PARTNER_HOTEL_ID],
    )
