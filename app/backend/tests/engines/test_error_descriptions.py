"""#266: engine operation errors are never empty and never carry passenger data."""

import pytest

from acme.engines.customer import EngineOperationError, build_customer_engine_app


async def test_declining_with_no_offer_names_the_error_class_and_reason(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    app = build_customer_engine_app()
    app.set_up()
    app.scenario_start("declines-upgrade")
    for _ in range(2):
        await app.scenario_step()  # cancel, notify: no offer exists
    app.scenarios.index = 4  # jump to the decline step, as the live failure did
    with pytest.raises(EngineOperationError) as err:
        await app.scenario_step()
    msg = str(err.value)
    assert msg.startswith("NoSentOffer: no offer sent for journey")
    assert "P-MARIANA" not in msg and "AC1234" not in msg


def test_unknown_error_text_is_not_leaked():
    from acme.engines.customer import describe_error

    msg = str(describe_error(KeyError("P-MARIANA")))
    assert msg.startswith("KeyError: operation failed") and "MARIANA" not in msg
