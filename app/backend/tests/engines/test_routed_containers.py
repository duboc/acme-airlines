"""#266 part 2b: with the caller sending preset and step, every preset completes when each call
lands on another container. Containers are independent engine apps with NO shared state, called
round-robin (ADR 0103); the result must match the single-process local mode."""

import inspect

import httpx
import pytest

from acme.api.app import create_app
from acme.api.engine_backend import EngineBackend, create_engine_app
from acme.engines.customer import build_customer_engine_app
from acme.engines.sales import build_sales_engine_app
from tests.api.test_engine_mode import IDS, STRUCTURAL

PID = "P-MARIANA"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ACME_STATE_DB", raising=False)
    monkeypatch.delenv("ENGINE_MODE", raising=False)


class Containers:
    """N independent Customer engines (no shared state), called round-robin: replicas/restarts."""

    def __init__(self, n=3):
        self.apps = []
        for _ in range(n):
            app = build_customer_engine_app()
            app.set_up()
            self.apps.append(app)
        self.i = 0
        self.pinned: int | None = None  # one Sales turn lands on one replica
        self.log: list[tuple[int, str]] = []

    def _next(self, op):
        if self.pinned is not None:
            idx = self.pinned
        else:
            idx = self.i % len(self.apps)
            self.i += 1
        self.log.append((idx, op))
        return self.apps[idx]

    async def call(self, op, **kwargs):
        out = getattr(self._next(op), op)(**kwargs)
        return await out if inspect.isawaitable(out) else out

    async def a2a_skill(self, skill, args):  # the Sales engine's handle on the Customer engine
        return await self._next("a2a_skill").a2a_skill(skill, args)


class Routed:
    def __init__(self, containers):
        self.c = containers

    async def call(self, op, **kwargs):
        return await self.c.call(op, **kwargs)

    def stream(self, **kwargs):
        return self.c._next("async_stream_query").async_stream_query(**kwargs)


class SalesRouted:
    def __init__(self, containers):
        self.containers = containers
        sales = build_sales_engine_app(customer_engine=containers)
        sales.set_up()
        self.sales = sales

    async def call(self, op, **kwargs):  # pragma: no cover - the backend only streams to Sales
        raise AssertionError(op)

    async def stream(self, **kwargs):
        c = self.containers
        c.pinned = c.i % len(c.apps)  # the Sales turn's calls all reach the same replica
        c.i += 1
        try:
            async for event in self.sales.async_stream_query(**kwargs):
                yield event
        finally:
            c.pinned = None


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def run(c, sid):
    state = (await c.post(f"/scenarios/{sid}/start")).json()
    while not state["done"]:
        r = await c.post(f"/scenarios/{sid}/step")
        assert r.status_code == 200, (sid, state["step_index"], r.text)
        state = r.json()
    journeys = (await c.get("/journeys")).json()
    phone = (await c.get(f"/whatsapp/{PID}/messages")).json()
    return state, journeys, [(m["direction"], bool(m["buttons"])) for m in phone]


@pytest.mark.parametrize("sid", IDS)
async def test_every_preset_completes_when_each_call_lands_on_another_container(sid):
    containers = Containers(3)
    backend = EngineBackend(Routed(containers), SalesRouted(containers))
    async with client(create_app()) as local, client(create_engine_app(backend)) as remote:
        want, want_journeys, want_phone = await run(local, sid)
        got, got_journeys, got_phone = await run(remote, sid)
    assert {k: got[k] for k in STRUCTURAL} == {k: want[k] for k in STRUCTURAL}
    assert got_journeys == want_journeys
    assert got_phone == want_phone  # the passenger sees the same conversation
    assert len({idx for idx, _ in containers.log}) == 3  # calls really were spread
