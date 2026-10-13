"""Cross-process demo state, part 1 (#200): the shared store and the cross-process lock."""

import asyncio
import subprocess
import sys
import textwrap
from datetime import datetime

import pytest

from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, GRU
from acme.llm.fake import FakeLlm
from acme.memory.store import SqliteJourneyStore
from acme.state.shared import LockTimeout, SharedState

PID = "P-MARIANA"


def worker(path):
    """A 'worker process': its own runtime and its own SharedState on the shared file."""
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    return rt, SharedState(str(path))


async def test_state_written_by_one_worker_is_seen_by_another(tmp_path):
    (a, sa), (b, sb) = worker(tmp_path / "s.db"), worker(tmp_path / "s.db")
    async with sa.operation(a):
        await a.trigger_cancellation("AC1234", "unscheduled maintenance")
    async with sb.operation(b):
        assert len(b.journeys) == 1
        (jid,) = b.journeys
        await b.handle_inbound(PID, "Perfect, thanks!")
    async with sa.operation(a):
        events = [e.type for e in a.journeys[jid].state.timeline]
        assert "passenger_message" in events and a.messages[PID][-1].direction == "inbound"


async def test_worker_with_stale_memory_catches_up_before_acting(tmp_path):
    (a, sa), (b, sb) = worker(tmp_path / "s.db"), worker(tmp_path / "s.db")
    async with sa.operation(a):
        await a.trigger_cancellation("AC1234", "x")
    async with sb.operation(b):
        pass  # b has seen v1
    async with sa.operation(a):
        a.clock.advance(datetime(2026, 10, 9, 22, 9, tzinfo=GRU) - a.clock.now())
        await a.tick()
    async with sb.operation(b):
        assert b.clock.now() == datetime(2026, 10, 9, 22, 9, tzinfo=GRU)
        assert len([m for m in b.messages[PID] if m.direction == "outbound"]) == 1


async def test_unchanged_state_is_not_rewritten(tmp_path):
    (a, sa) = worker(tmp_path / "s.db")
    async with sa.operation(a):
        await a.trigger_cancellation("AC1234", "x")
    v = sa.version()
    async with sa.operation(a):
        pass
    async with sa.operation(a):
        _ = a.messages
    assert sa.version() == v


async def test_journeys_live_in_the_existing_journey_store_table(tmp_path):
    (a, sa) = worker(tmp_path / "s.db")
    async with sa.operation(a):
        (jid,) = await a.trigger_cancellation("AC1234", "x")
    assert SqliteJourneyStore(str(tmp_path / "s.db")).ids() == [jid]
    async with sa.operation(a):
        a.reset()
    assert SqliteJourneyStore(str(tmp_path / "s.db")).ids() == []


async def test_operation_is_reentrant_within_a_task(tmp_path):
    (a, sa) = worker(tmp_path / "s.db")
    async with sa.operation(a):
        async with sa.operation(a):
            await a.trigger_cancellation("AC1234", "x")
    async with sa.operation(a):
        assert len(a.journeys) == 1


async def test_concurrent_operations_in_one_process_are_serialised(tmp_path):
    (a, sa) = worker(tmp_path / "s.db")
    inside, peak = 0, 0

    async def op():
        nonlocal inside, peak
        async with sa.operation(a):
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.02)
            inside -= 1

    await asyncio.gather(*[op() for _ in range(6)])
    assert peak == 1


async def test_a_cancelled_waiter_does_not_leak_the_lock(tmp_path):
    (a, sa) = worker(tmp_path / "s.db")
    release = asyncio.Event()

    async def holder():
        async with sa.operation(a):
            await release.wait()

    h = asyncio.create_task(holder())
    await asyncio.sleep(0.05)
    waiter = asyncio.create_task(sa.operation(a).__aenter__())
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await h
    async with sa.operation(a):  # still acquirable
        pass


async def test_lock_timeout_is_an_error_not_a_hang(tmp_path):
    (a, sa) = worker(tmp_path / "s.db")
    sb = SharedState(str(tmp_path / "s.db"), lock_timeout=0.2)
    async with sa.operation(a):
        with pytest.raises(LockTimeout):
            async with sb.operation(a):
                pass


def test_two_real_processes_never_overlap_in_the_critical_section(tmp_path):
    """flock across processes: 2 x 25 read-modify-write cycles on a counter file lose no update."""
    counter = tmp_path / "counter"
    counter.write_text("0")
    script = textwrap.dedent(
        f"""
        import sys, time
        from acme.state.shared import SharedState
        s = SharedState({str(tmp_path / "s.db")!r})
        for _ in range(25):
            with s.exclusive_sync():
                n = int(open({str(counter)!r}).read())
                time.sleep(0.002)
                open({str(counter)!r}, "w").write(str(n + 1))
        """
    )
    procs = [subprocess.Popen([sys.executable, "-W", "ignore", "-c", script]) for _ in range(2)]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    assert counter.read_text() == "50"


def test_sync_context_is_reentrant_and_blocks_other_holders(tmp_path):
    s1, s2 = (
        SharedState(str(tmp_path / "s.db")),
        SharedState(str(tmp_path / "s.db"), lock_timeout=0.2),
    )
    with s1.exclusive_sync():
        with s1.exclusive_sync():
            with pytest.raises(LockTimeout):
                with s2.exclusive_sync():
                    pass
    with s2.exclusive_sync():
        pass


def test_a_corrupt_snapshot_row_is_ignored_and_overwritten(tmp_path):
    import sqlite3

    path = tmp_path / "s.db"
    s = SharedState(str(path))
    db = sqlite3.connect(path)
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('snapshot', '{garbage')")
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('version', '7')")
    db.commit()
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    with s.operation_sync(rt):
        pass  # does not raise; state keeps this process's runtime
    assert s.version() >= 8
