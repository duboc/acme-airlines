from types import SimpleNamespace as R

from acme.api.scenarios import passenger_offer_status


def offers(*statuses):
    return [R(status=s) for s in statuses]


def test_rejected_duplicate_does_not_mask_a_sent_or_accepted_offer():
    assert passenger_offer_status(offers("sent", "rejected")) == "sent"
    assert passenger_offer_status(offers("accepted", "rejected", "rejected")) == "accepted"


def test_latest_passenger_facing_offer_wins():
    assert passenger_offer_status(offers("invalidated", "sent", "rejected")) == "sent"


def test_falls_back_to_latest_and_handles_empty():
    assert passenger_offer_status(offers("approved", "rejected")) == "rejected"
    assert passenger_offer_status([]) is None
