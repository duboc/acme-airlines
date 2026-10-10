from uuid import uuid4

import pytest
from pydantic import ValidationError

from acme.domain.models import Offer

VALID = dict(
    offer_id=uuid4(), journey_id="J-0a1b2c3d", flight="AC1252", cabin="premium_business",
    seat_hint="2A", price="135.00", standard_price="290.00", net_margin="80.00",
    currency="USD", origin="sales", optional=True,
)  # fmt: skip


def make(**over):
    return Offer(**{**VALID, **over})


def test_valid_offer_is_frozen():
    o = make()
    with pytest.raises(ValidationError):
        o.price = 1


def test_seat_hint_optional_and_margin_may_be_negative():
    assert make(seat_hint=None).seat_hint is None
    assert make(net_margin="-5.00").net_margin < 0


@pytest.mark.parametrize(
    "bad",
    [
        {"pitch": "Treat yourself!"},
        {"seat_hint": "2A — reply YES in 5 min!"},
        {"flight": "AC1252 hurry"},
        {"cabin": "anything"},
        {"journey_id": "J-xyz"},
        {"price": "10001"},
        {"price": "-1"},
        {"net_margin": "-10001"},
        {"currency": "EUR"},
        {"origin": "concierge"},
        {"optional": False},
    ],
)
def test_invalid_offers_rejected(bad):
    with pytest.raises(ValidationError):
        make(**bad)


def test_offer_has_no_free_text_field():
    assert not {"pitch", "headline", "description", "message", "text"} & set(Offer.model_fields)
