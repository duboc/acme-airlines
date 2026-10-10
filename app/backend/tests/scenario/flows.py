"""Given-step helpers: drive a journey to a named phase using only the ScenarioDriver."""

from __future__ import annotations

from datetime import datetime, timedelta

from acme.domain.fixtures import GRU, D
from acme.domain.journey import JourneyEvent
from acme.domain.models import Offer

from .driver import ACCEPT_UPGRADE_BUTTON, ScenarioDriver

D1 = D + timedelta(days=1)
AC1240_DEP = datetime(D1.year, D1.month, D1.day, 8, 15, tzinfo=GRU)
AC1252_DEP = datetime(D1.year, D1.month, D1.day, 11, 40, tzinfo=GRU)
NOTIFIED_AT = datetime(D.year, D.month, D.day, 22, 9, tzinfo=GRU)
NEW_CONSTRAINT = "Can I leave after 10:00 AM instead?"
ACK = "Thanks, that all works for me!"


def events(driver: ScenarioDriver, type_: str) -> list[JourneyEvent]:
    return [e for e in driver.get_journey().timeline if e.type == type_]


def sole(driver: ScenarioDriver, type_: str) -> JourneyEvent:
    found = events(driver, type_)
    assert len(found) == 1, f"expected exactly one {type_}, got {len(found)}"
    return found[0]


def cancelled(driver: ScenarioDriver) -> None:
    driver.trigger_cancellation("AC1234", "unscheduled maintenance")


def notified(driver: ScenarioDriver) -> None:
    """Resolution complete on AC1240 and the 22:09 WhatsApp message sent."""
    cancelled(driver)
    driver.advance_clock(NOTIFIED_AT)


def replanned(driver: ScenarioDriver) -> None:
    notified(driver)
    driver.passenger_reply(NEW_CONSTRAINT)


def acknowledged(driver: ScenarioDriver) -> None:
    replanned(driver)
    driver.passenger_reply(ACK)


def offer_delivered(driver: ScenarioDriver) -> Offer:
    acknowledged(driver)
    return driver.get_journey().offers[0].offer


def accepted(driver: ScenarioDriver) -> None:
    offer_delivered(driver)
    driver.passenger_tap(ACCEPT_UPGRADE_BUTTON)
