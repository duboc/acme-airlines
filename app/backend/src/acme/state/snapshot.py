"""JSON snapshot of the whole Customer runtime (#200).

Agent Engine runs 9 agent processes per container, so in-memory demo state is not shared between
turns. `capture` serialises everything a turn can change; `restore` loads it into another process's
runtime IN PLACE (object identity is kept: skills, runners and the scenario runner keep working).

Format: pydantic JSON only (`model_dump_json` / `model_validate_json`), no binary object
format: the snapshot is data from a file another process wrote, so loading it must not be able
to run code.
Journey state itself reuses the `journeys` table of `SqliteJourneyStore` (#16); the snapshot
holds the rest (world, simulator state, journey record metadata, cursor, clock, scenario cursor).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from pydantic import AwareDatetime, BaseModel

from acme.domain.clock import FixedClock
from acme.domain.models import HotelBooking, Voucher
from acme.memory.store import JourneyStore, dump_journey
from acme.sim.dcs import dcs_state

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime

SCHEMA_VERSION = 1


class JourneyMeta(BaseModel):
    passenger_id: str
    pnr_locator: str
    hotel: HotelBooking | None = None
    vouchers: list[Voucher] = []
    constraint_latest_arrival: AwareDatetime | None = None
    run_id: str


class ScenarioMeta(BaseModel):
    preset_id: str | None = None
    index: int = 0


class RuntimeSnapshot(BaseModel):
    schema_version: int = SCHEMA_VERSION
    clock: AwareDatetime | None = None  # only for a FixedClock (the demo's scripted time)
    sim: dict[str, Any]
    partners: dict[str, Any]
    dcs: dict[str, Any]
    cursor: int
    journeys: dict[str, JourneyMeta]
    journey_digest: str  # changes whenever any journey's persisted JSON changes
    scenario: ScenarioMeta | None = None


def capture(
    rt: CustomerRuntime, store: JourneyStore, scenarios: Any | None = None, *, save: bool = True
) -> str:
    """Snapshot JSON. Journey states are written to `store` (unless `save=False`)."""
    digest = hashlib.sha256()
    for _jid, rec in sorted(rt.journeys.items()):
        digest.update(dump_journey(rec.state).encode())
        if save:
            store.save(rec.state)
    if save:
        store.delete_except(set(rt.journeys))
    snap = RuntimeSnapshot(
        clock=rt.clock.now() if isinstance(rt.clock, FixedClock) else None,
        sim=rt.sim.export_data(),
        partners=rt.sim.partners.export_data(),
        dcs=dcs_state(rt.sim).export_data(),
        cursor=rt._cursor,  # noqa: SLF001
        journeys={
            jid: JourneyMeta(
                passenger_id=rec.passenger_id,
                pnr_locator=rec.pnr_locator,
                hotel=rec.hotel,
                vouchers=rec.vouchers,
                constraint_latest_arrival=rec.constraint_latest_arrival,
                run_id=rec.run_id,
            )
            for jid, rec in rt.journeys.items()
        },
        journey_digest=digest.hexdigest(),
        scenario=(
            ScenarioMeta(
                preset_id=scenarios.active.id if scenarios.active else None, index=scenarios.index
            )
            if scenarios is not None
            else None
        ),
    )
    return snap.model_dump_json()


def restore(
    rt: CustomerRuntime, text: str, store: JourneyStore, scenarios: Any | None = None
) -> None:
    """Load a snapshot into `rt` in place. Raises ValueError for anything that is not one."""
    from acme.api.scenarios import BY_ID
    from acme.customer.runtime import JourneyRecord

    snap = RuntimeSnapshot.model_validate_json(text)  # ValidationError is a ValueError
    if snap.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported snapshot schema {snap.schema_version}")
    if snap.clock is not None:
        if isinstance(rt.clock, FixedClock):
            rt.clock.set(snap.clock)
        else:
            rt.clock = FixedClock(snap.clock)
    rt.sim.clock = rt.clock
    rt.sim.import_data(snap.sim)
    rt.sim.partners.import_data(snap.partners)
    dcs_state(rt.sim).import_data(snap.dcs)
    rt._cursor = snap.cursor  # noqa: SLF001
    rt.journeys.clear()
    for jid, meta in snap.journeys.items():
        state = store.load(jid, rt.clock)
        if state is None:
            raise ValueError(f"snapshot references journey {jid} missing from the store")
        rt.journeys[jid] = JourneyRecord(
            state=state,
            passenger_id=meta.passenger_id,
            pnr_locator=meta.pnr_locator,
            hotel=meta.hotel,
            vouchers=list(meta.vouchers),
            constraint_latest_arrival=meta.constraint_latest_arrival,
            run_id=meta.run_id,
        )
    if scenarios is not None and snap.scenario is not None:
        scenarios.active = BY_ID.get(snap.scenario.preset_id) if snap.scenario.preset_id else None
        scenarios.index = snap.scenario.index
