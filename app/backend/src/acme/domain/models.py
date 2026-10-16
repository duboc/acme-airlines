"""Domain models shared by the simulators and agents."""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

Tier = Literal["basic", "silver", "gold", "diamond"]
FlightStatus = Literal["scheduled", "cancelled", "delayed", "departed", "arrived"]
Cabin = Literal["economy", "premium_business"]
Money = Annotated[Decimal, Field(ge=0, le=10000, decimal_places=2)]
Margin = Annotated[Decimal, Field(ge=-10000, le=10000, decimal_places=2)]


class LoyaltyProfile(BaseModel):
    member_id: str
    tier: Tier
    points: int = Field(ge=0)


class Passenger(BaseModel):
    passenger_id: str
    name: str
    tier: Tier
    loyalty: LoyaltyProfile | None = None
    phone: str | None = None
    origin_resident: bool = False  # DOC-06: ground transfer home, no hotel
    special_needs: bool = False  # DOC-07: PNAE, hotel and transfer even in daytime


class Flight(BaseModel):
    number: str
    origin: str
    destination: str
    scheduled_departure: AwareDatetime
    scheduled_arrival: AwareDatetime | None = None
    status: FlightStatus = "scheduled"
    # Seat map: open seats by cabin, e.g. {"economy": ["14A"], "premium_business": ["2A"]}.
    open_seats: dict[Cabin, list[str]] = Field(default_factory=dict)


class Segment(BaseModel):
    flight_number: str


class Bag(BaseModel):
    tag: str
    checked: bool = True
    routing: str | None = None  # the flight WorldTracer routes it to (filled in the journey view)


class SeatAssignment(BaseModel):
    flight_number: str
    seat: str
    cabin: Cabin = "economy"


class PNR(BaseModel):
    locator: str
    passenger_id: str
    segments: list[Segment]
    seats: list[SeatAssignment] = Field(default_factory=list)
    bags: list[Bag] = Field(default_factory=list)


class HotelBooking(BaseModel):
    booking_id: str
    hotel_id: str
    passenger_id: str
    nights: int = Field(ge=1)


class Transfer(BaseModel):
    transfer_id: str
    passenger_id: str
    origin: str
    destination: str


class Voucher(BaseModel):
    voucher_id: str
    passenger_id: str
    kind: Literal["meal", "hotel", "transport"]
    amount: Money


class CabinInventory(BaseModel):
    flight_number: str
    cabin: Cabin
    open_seats: int = Field(ge=0)
    standard_upgrade_fare: Money
    cost_floor: Money


class Offer(BaseModel):
    """Structured upsell offer. Deliberately has NO free-text field.

    Governance: Sales never authors passenger-facing text, so unknown fields are rejected
    and every field is constrained.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    offer_id: UUID
    journey_id: Annotated[str, StringConstraints(pattern=r"^J-[0-9a-f]{8}$")]
    flight: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}\d{3,4}$")]
    cabin: Literal["premium_business"]
    seat_hint: Annotated[str, StringConstraints(pattern=r"^\d{1,2}[A-K]$")] | None = None
    price: Money
    standard_price: Money
    net_margin: Margin
    currency: Literal["USD"]
    origin: Literal["sales"]
    optional: Literal[True]
