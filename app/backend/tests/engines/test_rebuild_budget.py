"""#280: how long a forced rebuild takes, per preset at its last step (offline, LLM_MODE=fake),
and that sim sessions reused by a replay do not leak state across runs.

The p95 ceiling below is INTERIM: a regression guard at about 1.6x the worst single rebuild measured
on the dev sandbox after the session reuse (614 ms). The target is p95 < 500 ms, see #280.
Why it is not flaky: 8 samples per preset (p95 is the slowest of 8 by nearest rank, so the
ceiling needs about 2x headroom: 1000 ms against a median of 514 ms and a max of 614 ms).
"""

import math
import sys

import pytest

from acme.api.scenarios import BY_ID
from acme.engines.customer import build_customer_engine_app

SAMPLES, CEILING_MS = 8, 1000.0
PID = "P-MARIANA"


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.delenv("ACME_STATE_DB", raising=False)
    monkeypatch.delenv("ENGINE_MODE", raising=False)


def measured_under_tracing() -> bool:
    """Coverage (or a debugger) slows every Python line about 2x: the timing would measure the
    tracer. CI runs this file again without coverage (`--no-cov`, see ci.yml) for the real check."""
    try:
        import coverage

        if coverage.Coverage.current() is not None:
            return True
    except ImportError:  # pragma: no cover
        pass
    return sys.gettrace() is not None


def p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


async def rebuild_ms(app, preset: str, steps: int) -> float:
    other = next(i for i in BY_ID if i != preset)
    app.scenario_start(other)  # park the engine elsewhere so the call below must rebuild
    app.scenario_state(preset=preset, step=steps)
    assert app.last_rebuild["preset"] == preset and app.last_rebuild["to_step"] == steps
    return app.last_rebuild["ms"]


async def test_rebuild_p95_per_preset_stays_under_the_interim_ceiling(capsys):
    app = build_customer_engine_app()
    app.set_up()
    rows, failures = [], []
    for preset, p in BY_ID.items():
        times = [await rebuild_ms(app, preset, len(p.steps)) for _ in range(SAMPLES)]
        rows.append(f"{preset} ({len(p.steps)} steps): median {sorted(times)[SAMPLES // 2]:.0f} "
                    f"p95 {p95(times):.0f} max {max(times):.0f} ms")  # fmt: skip
        if p95(times) >= CEILING_MS:
            failures.append(rows[-1])
    with capsys.disabled():
        print(
            "\nrebuild latency (target p95 < 500 ms, interim ceiling 1000 ms):\n  "
            + "\n  ".join(rows)
        )
    if measured_under_tracing():
        pytest.skip("timing is not asserted under coverage/tracing; the no-coverage CI step does")
    assert not failures, failures


async def view(app, preset: str, steps: int):
    msgs = await app.whatsapp_messages(passenger_id=PID, preset=preset, step=steps)
    return (
        {k: v for k, v in app.scenario_state(preset=preset, step=steps).items()},
        app.journeys_list(preset=preset, step=steps),
        [{k: v for k, v in m.items() if k != "message_id"} for m in msgs],
    )


async def test_a_second_rebuild_equals_one_on_a_fresh_container():
    """Sessions are reused only inside one replay: a used engine and a fresh one agree."""
    used, fresh = build_customer_engine_app(), build_customer_engine_app()
    used.set_up()
    fresh.set_up()
    for preset in ("happy-path", "seat-taken", "happy-path"):  # same preset again, after another
        used.scenario_start("declines-upgrade")
        want = await view(fresh, preset, len(BY_ID[preset].steps))
        assert await view(used, preset, len(BY_ID[preset].steps)) == want


async def test_a_rebuild_is_exported_as_a_metric_and_shown_in_diagnostics(metric_points):
    app = build_customer_engine_app()
    app.set_up()
    assert app.diagnostics()["last_rebuild"] is None
    app.scenario_start("seat-taken")
    app.scenario_state(preset="happy-path", step=2)  # recorded although the replay is silenced
    last = app.diagnostics()["last_rebuild"]
    assert (last["preset"], last["to_step"], last["reason"]) == ("happy-path", 2, "preset_mismatch")
    pts = metric_points()["acme.scenario.rebuild.duration"]
    assert any(a == {"preset": "happy-path", "reason": "preset_mismatch"} for a, _ in pts)
