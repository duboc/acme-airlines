"""#279, live run 8: the replay tape lived in each worker process's memory, and the caller's tape
came from whichever worker answered its last call, so a forced rebuild missed the LLM turns that
ran elsewhere and re-worded the Customer's own messages. The tape now travels in the shared
snapshot (and a reset drops it)."""

import inspect
import json
from datetime import UTC, datetime

import pytest

from acme.api.engine_backend import EngineBackend
from acme.engines import customer as customer_mod
from acme.engines.customer import build_customer_engine_app
from acme.engines.sales import build_sales_engine_app
from acme.llm.fake import FakeLlm
from acme.state.snapshot import RuntimeSnapshot
from tests.live.agent_engine_proof import run_rebuild_proof


class Creative(FakeLlm):
    async def compose_passenger_message(self, context):
        return "Creative: " + await super().compose_passenger_message(context)


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("ACME_STATE_DB", str(tmp_path / "state" / "s.db"))


def workers(n=4):
    apps = []
    for _ in range(n):  # the worker processes of one container: own memory, one shared file
        app = build_customer_engine_app()
        app.set_up()
        app.runtime.llm.inner = Creative()
        apps.append(app)
    return apps


class Alternating:
    """Each call lands on the next worker, like Agent Engine's routing inside a container."""

    def __init__(self, apps):
        self.apps, self.i = apps, 0

    def _next(self):
        app, self.i = self.apps[self.i % len(self.apps)], self.i + 1
        return app

    async def call(self, op, **kwargs):
        out = getattr(self._next(), op)(**kwargs)
        return await out if inspect.isawaitable(out) else out

    def stream(self, **kwargs):  # the Sales engine goes to its own worker
        return self.apps[0].async_stream_query(**kwargs)


def backend_over(apps, monkeypatch):
    monkeypatch.delenv("ACME_STATE_DB")  # Sales keeps its own memory: it must not take the lock
    sales = build_sales_engine_app(customer_engine=apps[0])  # a Customer op calls it, in-process
    sales.set_up()
    from tests.api.test_engine_mode import LocalEngine

    return EngineBackend(Alternating(apps), LocalEngine(sales))


async def test_the_tape_is_shared_by_every_worker_of_the_container(state_env):
    a, b, c, d = workers()
    a.scenario_start("happy-path")
    await b.scenario_step()  # cancel
    await c.scenario_step()  # notify: the model composes the notice on this worker only
    tape = d.scenario_state()["tape"]  # a worker that ran no LLM turn
    assert [e["m"] for e in tape] == ["compose"] and tape[0]["text"].startswith("Creative")
    await a.scenario_step()  # after 10am
    assert [e["m"] for e in b.scenario_state()["tape"]] == ["compose", "parse"]


async def test_a_forced_rebuild_after_a_run_spread_over_workers_replays_every_turn(
    state_env, monkeypatch
):
    apps = workers()
    backend = backend_over(apps, monkeypatch)
    log = []
    monkeypatch.setattr(customer_mod, "diag", lambda event, **kw: log.append((event, kw)))

    def logs(since):
        now = datetime.now(UTC).isoformat()
        return [{"ts": now, "text": "ACME_DIAG " + json.dumps({"event": e, **kw})}
                for e, kw in log if e == "rebuild"]  # fmt: skip

    report = await run_rebuild_proof(backend, logs, "happy-path", "seat-taken", wait=0.0)
    assert report.failures == []  # transcript, journeys and state identical
    (rebuild,) = [kw for e, kw in log if e == "rebuild"]
    assert rebuild["tape_miss"] == 0 and rebuild["tape_discarded"] == 0


async def test_a_reset_drops_the_tape_everywhere(state_env):
    a, b, c, _ = workers()
    a.scenario_start("happy-path")
    await b.scenario_step()
    await c.scenario_step()
    assert a.scenario_state()["tape"]
    b.reset()  # on another worker than the one that recorded
    a.scenario_start("happy-path")
    assert a.scenario_state()["tape"] == [] and c.scenario_state()["tape"] == []


async def test_a_backend_reset_forgets_the_tape_it_holds(state_env, monkeypatch):
    apps = workers(2)
    backend = backend_over(apps, monkeypatch)
    await backend.scenario_start("happy-path")
    await backend.scenario_step("happy-path")
    await backend.scenario_step("happy-path")
    assert backend._tape  # noqa: SLF001
    await backend.reset()
    assert backend._tape is None  # noqa: SLF001


async def test_the_tape_of_a_step_response_is_kept_even_if_the_next_call_has_none(
    state_env, monkeypatch
):
    apps = workers(2)
    backend = backend_over(apps, monkeypatch)
    await backend.scenario_start("happy-path")
    await backend.scenario_step("happy-path")
    await backend.scenario_step("happy-path")  # notify composes with the model
    assert backend._tape and backend._tape[0]["m"] == "compose"  # noqa: SLF001


def test_a_snapshot_written_before_the_tape_existed_still_loads():
    old = {"sim": {}, "partners": {}, "dcs": {}, "cursor": 0, "journeys": {}, "journey_digest": ""}
    assert RuntimeSnapshot.model_validate(old).tape == []
