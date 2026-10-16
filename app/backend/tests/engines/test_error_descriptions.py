"""#266: engine operation errors are never empty and never carry passenger data."""

import pytest

from acme.customer.conversion import NoSentOffer
from acme.engines.customer import EngineOperationError, build_customer_engine_app, describe_error


async def test_declining_with_no_offer_names_the_error_class_and_reason(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    app = build_customer_engine_app()
    app.set_up()
    app.scenario_start("declines-upgrade")
    for _ in range(2):
        await app.scenario_step()  # cancel, notify: no offer exists
    app.scenarios.index = 4  # jump to the decline step, as the live failure did
    out = await app.scenario_step()  # #455: NoSentOffer is 409 error-as-data, not a 503
    assert out["error"] == {
        "status": 409,
        "code": "no_offer_sent",
        "detail": "no offer has been sent to this passenger",
    }
    app.runtime.journeys.clear()  # invariant violated inside the step -> EngineOperationError
    with pytest.raises(EngineOperationError) as err:
        await app.scenario_step()
    msg = str(err.value)
    assert msg.startswith("AssertionError: invariant violated")
    assert "P-MARIANA" not in msg and "AC1234" not in msg


def test_unknown_error_text_is_not_leaked():
    msg = str(describe_error(KeyError("P-MARIANA")))
    assert msg.startswith("KeyError: operation failed") and "MARIANA" not in msg
    no_offer = str(describe_error(NoSentOffer("P-MARIANA")))
    assert (
        no_offer.startswith("NoSentOffer: no offer sent for journey") and "MARIANA" not in no_offer
    )
