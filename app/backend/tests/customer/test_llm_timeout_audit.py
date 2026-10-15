"""#281: the LLM timeout fallback (#231) pins what the bounded wrapper passes on, logs, labels and
counts, plus the timeout-setting boundaries."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from acme.llm import bounded
from acme.llm.bounded import DEFAULT_TIMEOUT_S, BoundedLlm, LlmTimeout, load_timeout
from acme.llm.port import Acknowledge, Other


class Spy:
    def __init__(self, delay=0.0):
        self.delay, self.seen = delay, []

    async def compose_passenger_message(self, context):
        self.seen.append(("compose", context))
        await asyncio.sleep(self.delay)
        return "text"

    async def parse_passenger_request(self, text):
        self.seen.append(("parse", text))
        await asyncio.sleep(self.delay)
        return Acknowledge()


class FakeSpan:
    def __init__(self):
        self.attrs = {}

    def set_attribute(self, key, value):
        self.attrs[key] = value


@pytest.fixture
def span(monkeypatch):
    fake = FakeSpan()
    monkeypatch.setattr(bounded, "trace", SimpleNamespace(get_current_span=lambda: fake))
    return fake


@pytest.fixture
def waits(monkeypatch):
    """The timeout every model call is bounded with."""
    seen, real = [], asyncio.wait_for

    async def spy(aw, timeout):
        seen.append(timeout)
        return await real(aw, timeout)

    monkeypatch.setattr(bounded.asyncio, "wait_for", spy)
    return seen


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("0.5", 0.5),  # any positive value is kept
        ("0", DEFAULT_TIMEOUT_S),  # zero never means "no wait"
        ("-3", DEFAULT_TIMEOUT_S),
        ("abc", DEFAULT_TIMEOUT_S),
        ("", DEFAULT_TIMEOUT_S),
        (None, DEFAULT_TIMEOUT_S),
    ],
)
def test_given_the_timeout_setting_when_loaded_then_only_a_positive_number_is_kept(raw, want):
    env = {} if raw is None else {"LLM_TIMEOUT_SECONDS": raw}
    assert load_timeout(env) == want


async def test_given_a_fast_model_when_composing_then_it_gets_the_context_and_the_bound(waits):
    spy = Spy()
    assert await BoundedLlm(spy, 2.5).compose_passenger_message({"a": 1}) == "text"
    assert spy.seen == [("compose", {"a": 1})] and waits == [2.5]


async def test_given_a_fast_model_when_parsing_then_it_gets_the_text_and_the_bound(waits):
    spy = Spy()
    assert isinstance(await BoundedLlm(spy, 2.5).parse_passenger_request("hi"), Acknowledge)
    assert spy.seen == [("parse", "hi")] and waits == [2.5]


async def test_given_a_slow_compose_when_it_times_out_then_it_raises_logs_and_marks_the_span(
    caplog, span
):
    with (
        caplog.at_level(logging.WARNING, logger="acme.llm.bounded"),
        pytest.raises(LlmTimeout) as e,
    ):
        await BoundedLlm(Spy(delay=1), 0.01).compose_passenger_message({})
    assert str(e.value) == "compose timed out" and e.value.__suppress_context__
    assert caplog.messages == ["llm_timeout op=compose timeout_s=0.01 fallback=true"]
    assert span.attrs == {"acme.llm.timeout": True, "acme.llm.op": "compose"}


async def test_given_a_slow_parse_when_it_times_out_then_it_fails_closed_and_counts_the_fallback(
    caplog, span, metric_points
):
    def count():
        points = metric_points().get("acme.llm.template_fallbacks", [])
        return next((v for a, v in points if a == {"surface": "parse_timeout"}), 0)

    before = count()
    with caplog.at_level(logging.WARNING, logger="acme.llm.bounded"):
        got = await BoundedLlm(Spy(delay=1), 0.01).parse_passenger_request("after 10am")
    assert got == Other(text="after 10am")
    assert caplog.messages == ["llm_timeout op=parse timeout_s=0.01 fallback=true"]
    assert span.attrs == {"acme.llm.timeout": True, "acme.llm.op": "parse"}
    assert count() == before + 1
