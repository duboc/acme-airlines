"""#266: the live proof's call sequence (all presets, one Sales + two Customer workers)."""

import pytest

from acme.api.scenarios import PRESETS
from acme.engines.customer import build_customer_engine_app
from acme.engines.sales import build_sales_engine_app


class Handle:
    def __init__(self, app):
        self.app = app

    async def a2a_skill(self, skill, args):
        return await self.app.a2a_skill(skill, args)


@pytest.mark.parametrize("shared", [True, False])
async def test_every_preset_runs_all_its_steps_back_to_back(tmp_path, monkeypatch, shared):
    monkeypatch.delenv("LLM_MODE", raising=False)
    if shared:
        monkeypatch.setenv("ACME_STATE_DB", str(tmp_path / "c" / "state.db"))
    cust, other = build_customer_engine_app(), build_customer_engine_app()
    cust.set_up()
    other.set_up()
    if shared:
        monkeypatch.setenv("ACME_STATE_DB", str(tmp_path / "s" / "state.db"))
    sales = build_sales_engine_app(Handle(cust))
    sales.set_up()
    for preset in PRESETS:
        cust.scenario_start(preset.id)
        for turn, _step in enumerate(preset.steps, 1):
            worker = (cust, other)[turn % 2 if shared else 0]
            await worker.scenario_step()  # raises on turn N when no offer was sent
            _ = [e async for e in sales.async_stream_query(message="tick", user_id="demo")]
        assert cust.scenario_state()["done"], preset.id
