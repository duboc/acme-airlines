"""Connection feasibility (minimum connection time). Pure, deterministic, no I/O, no LLM.

A connection is feasible when `onward_departure - arrival >= MCT`. The margin is the slack
over the MCT in minutes (negative when infeasible). MCT is configurable per airport; the
default international MCT at SCL is 90 minutes (see `acme.domain.fixtures`).
"""

from dataclasses import dataclass
from datetime import datetime

from acme.domain.models import Flight

DEFAULT_MCT_MINUTES: dict[str, int] = {"SCL": 90}
FALLBACK_MCT_MINUTES = 90


@dataclass(frozen=True)
class ConnectionResult:
    feasible: bool
    connection_minutes: int
    mct_minutes: int
    margin_minutes: int


def check_connection(
    airport: str,
    arrival: datetime,
    onward_departure: datetime,
    mct_minutes: int | None = None,
) -> ConnectionResult:
    for name, value in (("arrival", arrival), ("onward_departure", onward_departure)):
        if value.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware")
    mct = (
        mct_minutes
        if mct_minutes is not None
        else DEFAULT_MCT_MINUTES.get(airport, FALLBACK_MCT_MINUTES)
    )
    connection = int((onward_departure - arrival).total_seconds() // 60)
    margin = connection - mct
    return ConnectionResult(margin >= 0, connection, mct, margin)


def check_flights(
    arriving: Flight, onward: Flight, mct_minutes: int | None = None
) -> ConnectionResult:
    if arriving.destination != onward.origin:
        raise ValueError("flights must connect at the same airport")
    if arriving.scheduled_arrival is None:
        raise ValueError(f"{arriving.number} has no scheduled arrival")
    return check_connection(
        arriving.destination, arriving.scheduled_arrival, onward.scheduled_departure, mct_minutes
    )
