from __future__ import annotations

import importlib

import pytest

from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT

from .driver import ScenarioDriver


@pytest.fixture
def clock() -> FixedClock:
    """Frozen at the OCC cancellation instant, 22:03 on D."""
    return FixedClock(DISRUPTION_DETECTED_AT)


@pytest.fixture
def driver(clock: FixedClock) -> ScenarioDriver:
    """The real runtime driver (fixed clock, FakeLlm, in-process MCP).

    Until a backend issue ships ``acme.runtime.scenario.make_driver`` this raises
    NotImplementedError, which the strict-xfail marks on the scenario tests expect.
    """
    try:
        module = importlib.import_module("acme.runtime.scenario")
    except ModuleNotFoundError as exc:
        raise NotImplementedError("acme.runtime.scenario is not implemented yet") from exc
    d: ScenarioDriver = module.make_driver(clock)
    d.reset()
    return d
