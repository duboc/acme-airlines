"""#281: the Sales route guard (#230) pins its audit record, metric, response and scope handling,
not just the 403. Direct ASGI calls, so each detail can be asserted."""

import logging

import pytest

from acme.api.a2a_guard import SalesRouteGuard, sales_token_from_env

NAME = "acme.guardrail.decisions"
TOKEN = "sales-secret-token-0123456789"


def scope(kind="http", path="/journeys", method="GET", auth=f"Bearer {TOKEN}"):
    out = {
        "type": kind,
        "path": path,
        "headers": [(b"authorization", auth.encode())] if auth else [],
    }
    if kind == "http":
        out["method"] = method
    return out


class Recorder:
    def __init__(self):
        self.reached, self.sent = [], []

    async def inner(self, scope, receive, send):
        self.reached.append(scope["path"])

    async def send(self, message):
        self.sent.append(message)

    async def call(self, sc, token=TOKEN):
        await SalesRouteGuard(self.inner, token)(sc, None, self.send)
        return self


@pytest.mark.parametrize(
    ("env", "want"),
    [
        ({"ACME_SALES_TOKEN": "abc"}, "abc"),
        ({"ACME_SALES_TOKEN": ""}, None),
        ({}, None),
        ({"acme_sales_token": "abc"}, None),  # the variable name is exact
    ],
)
def test_given_the_environment_when_reading_the_sales_token_then_only_the_exact_name_counts(
    env, want
):
    assert sales_token_from_env(env) == want


async def test_given_a_denied_sales_route_when_audited_then_the_record_names_route_and_method(
    caplog,
):
    with caplog.at_level(logging.WARNING, logger="acme.audit"):
        rec = await Recorder().call(scope(path="/journeys", method="GET"))
    (record,) = [r for r in caplog.records if r.name == "acme.audit"]
    assert record.getMessage() == (
        "guardrail audit: sales caller denied route=/journeys method=GET"
    )
    assert (record.event, record.caller) == ("sales_route_denied", "sales")
    assert TOKEN not in caplog.text and rec.reached == []


async def test_given_a_sales_websocket_when_it_connects_then_it_is_denied_as_ws(caplog):
    with caplog.at_level(logging.WARNING, logger="acme.audit"):
        rec = await Recorder().call(scope(kind="websocket", path="/a2a"))
    assert rec.reached == []  # even on /a2a: only an http POST qualifies
    assert caplog.records[-1].getMessage().endswith("route=/a2a method=WS")


async def test_given_a_denied_sales_route_then_the_response_is_a_plain_403_forbidden():
    rec = await Recorder().call(scope())
    start = next(m for m in rec.sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in rec.sent if m["type"] == "http.response.body")
    assert start["status"] == 403 and body == b"forbidden"
    assert (b"content-type", b"text/plain; charset=utf-8") in start["headers"]


async def test_given_a_denied_sales_route_then_the_guardrail_metric_counts_a_route_rejection(
    metric_points,
):
    attrs = {"rule": "sales_route_denied", "outcome": "reject", "step": "route"}

    def count():
        return next(
            (v for a, v in metric_points().get(NAME, []) if a == attrs),
            0,
        )

    before = count()
    await Recorder().call(scope())
    assert count() == before + 1


async def test_given_sales_posts_a2a_then_it_passes_and_nothing_is_audited(caplog):
    with caplog.at_level(logging.WARNING, logger="acme.audit"):
        rec = await Recorder().call(scope(path="/a2a", method="POST"))
    assert rec.reached == ["/a2a"] and rec.sent == []
    assert not [r for r in caplog.records if r.name == "acme.audit"]
