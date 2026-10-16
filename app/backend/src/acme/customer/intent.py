"""The passenger's intent (#231, #279): the model decides, the time pattern only rescues `Other`.

Kept apart from `outreach.py` so mutation testing (#281) stays focused on this decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from acme.llm.fake import can_be_acknowledgement, departure_after
from acme.llm.port import Acknowledge, DepartureAfter, Other, StructuredRequest

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime


async def classify(rt: CustomerRuntime, text: str) -> StructuredRequest:
    """The passenger's intent. Depends only on the text, so callers run it outside any lock.

    The model decides. Only when it answers Other (including malformed output, an exception or a
    timeout: fail closed) and the deterministic time pattern reads the text does the pattern win,
    so one bad model answer cannot lose the request. A non-Other answer is never overridden."""
    try:
        result = await rt.llm.parse_passenger_request(text)
    except Exception:  # noqa: BLE001 (fail closed: never an acknowledgement)
        result = Other(text=text)
    if isinstance(result, Acknowledge) and not can_be_acknowledgement(text):
        result = Other(text=text)  # a negated, long, question or injected text: never an ack (#421)
    source = "model"
    if isinstance(result, Other) and (after := departure_after(text)) is not None:
        result, source = after, "pattern"
    kind = {DepartureAfter: "departure_after", Acknowledge: "ack"}.get(type(result), "other")
    rt.last_parse = {"parse_kind": kind, "parse_source": source}  # no text: for the op diag
    return result
