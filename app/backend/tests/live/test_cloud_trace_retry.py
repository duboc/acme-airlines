"""CloudTraceV1 retry/backoff (#211): no network, fake session, injected sleep and jitter."""

from datetime import UTC, datetime

import pytest

from tests.live.agent_engine_proof import CloudTraceV1, TraceQueryFailed

START = datetime(2026, 10, 13, 3, 15, tzinfo=UTC)
END = datetime(2026, 10, 13, 3, 16, tzinfo=UTC)
OK_BODY = {"traces": [{"traceId": "t1", "spans": [{"name": "acme.x", "labels": {"k": "v"}}]}]}


class HTTPError(OSError):
    """Stands in for requests.HTTPError (an OSError subclass)."""


class Response:
    def __init__(self, status: int, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(f"{self.status_code} Client Error")

    def json(self):
        return self._body


class Session:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client(session, sleeps, **kw):
    return CloudTraceV1("p", session=session, sleep=sleeps.append, jitter=lambda: 1.0, **kw)


def test_429_then_success_retries_with_exponential_backoff():
    sleeps: list[float] = []
    session = Session(Response(429), Response(429), Response(200, OK_BODY))
    traces = client(session, sleeps)._list(START, END)
    assert [t.trace_id for t in traces] == ["t1"] and session.calls == 3
    assert sleeps == [2.0, 4.0]  # base * 2**attempt, jitter pinned at the ceiling


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_5xx_is_retried(status):
    sleeps: list[float] = []
    session = Session(Response(status), Response(200, OK_BODY))
    assert client(session, sleeps)._list(START, END)[0].trace_id == "t1"
    assert len(sleeps) == 1


def test_network_errors_are_retried():
    sleeps: list[float] = []
    session = Session(ConnectionError("reset"), TimeoutError("slow"), Response(200, OK_BODY))
    assert client(session, sleeps)._list(START, END)
    assert len(sleeps) == 2


def test_retry_after_header_is_honoured_and_capped():
    sleeps: list[float] = []
    session = Session(
        Response(429, headers={"Retry-After": "7"}),
        Response(429, headers={"Retry-After": "9999"}),
        Response(200, OK_BODY),
    )
    client(session, sleeps, cap=30.0)._list(START, END)
    assert sleeps == [7.0, 30.0]


def test_jitter_scales_the_delay_between_half_and_full():
    sleeps: list[float] = []
    session = Session(Response(429), Response(200, OK_BODY))
    CloudTraceV1("p", session=session, sleep=sleeps.append, jitter=lambda: 0.0)._list(START, END)
    assert sleeps == [1.0]  # 2.0 ceiling * 0.5


def test_exhausted_attempts_raise_a_clear_error():
    sleeps: list[float] = []
    session = Session(*[Response(429)] * 3)
    with pytest.raises(TraceQueryFailed, match=r"3 attempt.*HTTP 429"):
        client(session, sleeps, max_attempts=3)._list(START, END)
    assert session.calls == 3 and len(sleeps) == 2


def test_total_backoff_budget_is_bounded():
    sleeps: list[float] = []
    session = Session(*[Response(429)] * 10)
    with pytest.raises(TraceQueryFailed):
        client(session, sleeps, max_attempts=10, max_total=5.0)._list(START, END)
    assert sum(sleeps) <= 5.0 and session.calls < 10


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_transient_4xx_fails_immediately_without_retry(status):
    sleeps: list[float] = []
    session = Session(Response(status), Response(200, OK_BODY))
    with pytest.raises(HTTPError):
        client(session, sleeps)._list(START, END)
    assert session.calls == 1 and sleeps == []


def test_a_page_token_request_is_also_retried():
    sleeps: list[float] = []
    page1 = Response(200, {**OK_BODY, "nextPageToken": "n"})
    session = Session(page1, Response(429), Response(200, OK_BODY))
    assert len(client(session, sleeps)._list(START, END)) == 2
    assert sleeps == [2.0]
