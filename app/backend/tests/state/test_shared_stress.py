"""#288: several OS processes read and write one SharedState file at once. No `database is
locked`, no lost update (every write advances the shared clock by one second and the version by
one), and a process never observes a version older than one it already saw.

`test_four_processes_...` is the quick regression (PR suite, a few seconds); the same worker runs
for about 30 s in `-m stress` (not part of the default suite)."""

import json
import subprocess
import sys
import textwrap
from datetime import timedelta

import pytest

from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.llm.fake import FakeLlm
from acme.state.shared import SharedState

PROCS = 4

WORKER = textwrap.dedent(
    """
    import json, sys, time
    from datetime import timedelta
    from acme.customer.runtime import CustomerRuntime
    from acme.domain.clock import FixedClock
    from acme.domain.fixtures import DISRUPTION_DETECTED_AT
    from acme.llm.fake import FakeLlm
    from acme.state.shared import SharedState

    path, mode, amount = sys.argv[1], sys.argv[2], float(sys.argv[3])
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    out = {"errors": [], "writes": 0, "versions": [], "reads": 0, "regressions": 0}
    deadline = time.monotonic() + amount if mode == "seconds" else None
    n = 0
    try:
        state = SharedState(path)
        seen = 0
        while (n < amount) if deadline is None else (time.monotonic() < deadline):
            n += 1
            if n % 3 == 0:  # a read-only operation: loads, writes nothing
                with state.operation_sync(rt):
                    clock = rt.clock.now()
                out["reads"] += 1
                v = state.version()
            else:
                with state.operation_sync(rt):
                    rt.clock.advance(timedelta(seconds=1))
                out["writes"] += 1
                v = state.version()
            if v < seen:
                out["regressions"] += 1
            seen = max(seen, v)
            out["versions"].append(v)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"{type(exc).__name__}: {exc}"[:200])
    print(json.dumps(out))
    """
)


def run_workers(path, mode, amount):
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(path), mode, str(amount)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )  # fmt: skip
        for _ in range(PROCS)
    ]
    results = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=180)
        assert p.returncode == 0, stderr[-500:]
        results.append(json.loads(stdout.strip().splitlines()[-1]))
    return results


def check(path, results):
    assert [r["errors"] for r in results] == [[]] * PROCS  # no "database is locked", no timeout
    assert all(r["regressions"] == 0 for r in results)  # a process never sees an older version
    writes = sum(r["writes"] for r in results)
    assert writes > PROCS  # the workers really ran
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    final = SharedState(str(path))
    with final.operation_sync(rt):
        clock = rt.clock.now()
    assert clock == DISRUPTION_DETECTED_AT + timedelta(seconds=writes)  # no lost update
    assert final.version() == writes  # one version per write, none skipped, none repeated
    for r in results:  # strictly increasing for the writes a process made, never decreasing
        assert r["versions"] == sorted(r["versions"])


def test_four_processes_writing_and_reading_one_file_lose_nothing(tmp_path):
    path = tmp_path / "s.db"
    check(path, run_workers(path, "ops", 15))


@pytest.mark.stress
def test_four_processes_hammer_one_file_for_thirty_seconds(tmp_path):
    path = tmp_path / "s.db"
    check(path, run_workers(path, "seconds", 30))


def test_the_connection_is_wal_with_a_busy_timeout(tmp_path):
    state = SharedState(str(tmp_path / "s.db"))
    assert state._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"  # noqa: SLF001
    assert state._db.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000  # noqa: SLF001
    assert state._db.execute("PRAGMA synchronous").fetchone()[0] == 1  # noqa: SLF001 NORMAL


def test_a_failed_write_rolls_back_journeys_and_snapshot_together(tmp_path, monkeypatch):
    state = SharedState(str(tmp_path / "s.db"))
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    with state.operation_sync(rt):
        rt.clock.advance(timedelta(seconds=1))
    before = state.version()
    real = state._journeys.delete_except  # noqa: SLF001

    def boom(keep):
        real(keep)
        raise RuntimeError("disk full")

    monkeypatch.setattr(state._journeys, "delete_except", boom)  # noqa: SLF001
    with pytest.raises(RuntimeError), state.operation_sync(rt):
        rt.clock.advance(timedelta(seconds=1))
    assert state.version() == before and not state._db.in_transaction  # noqa: SLF001
    other = SharedState(str(tmp_path / "s.db"))
    assert other.version() == before  # nothing half-written is visible


def test_a_reader_never_goes_back_to_an_older_version(tmp_path):
    state = SharedState(str(tmp_path / "s.db"))
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    for _ in range(3):
        with state.operation_sync(rt):
            rt.clock.advance(timedelta(seconds=1))
    assert state.version() == 3
    state._db.execute("UPDATE meta SET value = '1' WHERE key = 'version'")  # noqa: SLF001
    assert state.version() == 3  # a stale or reset file cannot make a reader regress
    with state.operation_sync(rt):
        assert rt.clock.now() == DISRUPTION_DETECTED_AT + timedelta(seconds=3)  # not reloaded older
