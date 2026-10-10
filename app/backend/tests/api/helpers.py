from uuid import uuid4

from acme.domain.models import Offer


def put_sent_offer(state):
    rec = state.add_offer(
        Offer(
            offer_id=uuid4(),
            journey_id=state.journey_id,
            flight="AC1252",
            cabin="premium_business",
            price="135",
            standard_price="290",
            net_margin="80",
            currency="USD",
            origin="sales",
            optional=True,
        )  # fmt: skip
    )
    rec.status = "sent"
    return rec
