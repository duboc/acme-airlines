"""CloudTraceV1 retry/backoff (#211): no network, fake session, injected sleep and jitter."""

from datetime import UTC, datetime

import pytest

from tests.live.agent_engine_proof import CloudTraceV1, TraceListTruncated, TraceQueryFailed

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


# --- pagination, page cap and the server-side filter (#217) -------------------------------------
def page(trace_ids, token=None):
    body = {"traces": [{"traceId": t, "spans": [{"name": "acme.x"}]} for t in trace_ids]}
    return Response(200, {**body, **({"nextPageToken": token} if token else {})})


class RecordingSession(Session):
    def __init__(self, *outcomes):
        super().__init__(*outcomes)
        self.params: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.params.append(dict(params))
        return super().get(url, params=params, timeout=timeout)


def test_listing_follows_next_page_token_across_three_pages():
    session = RecordingSession(page(["a"], "p2"), page(["b"], "p3"), page(["c"]))
    c = client(session, [])
    assert [t.trace_id for t in c._list(START, END)] == ["a", "b", "c"]
    assert [p.get("pageToken") for p in session.params] == [None, "p2", "p3"]
    assert c.pages_fetched == 3 and c.cap_hit is False


def test_hitting_the_page_cap_raises_instead_of_truncating():
    session = RecordingSession(*[page(["x"], "more")] * 5)
    c = client(session, [], max_pages=3)
    with pytest.raises(TraceListTruncated, match=r"after 3 pages"):
        c._list(START, END)
    assert session.calls == 3 and c.cap_hit is True and c.pages_fetched == 3


def test_the_server_side_span_filter_is_sent_on_every_page():
    session = RecordingSession(page(["a"], "p2"), page(["b"]))
    client(session, [])._list(START, END)
    assert [p["filter"] for p in session.params] == ["span:acme.", "span:acme."]
    assert session.params[0]["pageSize"] == 100 and session.params[0]["view"] == "COMPLETE"


def test_the_filter_prefix_is_configurable_and_can_be_disabled():
    session = RecordingSession(page(["a"]), page(["a"]))
    client(session, [], span_prefix="acme.scenario.")._list(START, END)
    client(session, [], span_prefix="")._list(START, END)
    assert (
        session.params[0]["filter"] == "span:acme.scenario." and "filter" not in session.params[1]
    )


def test_a_429_on_page_two_is_retried_and_paging_continues():
    sleeps: list[float] = []
    session = RecordingSession(page(["a"], "p2"), Response(429), page(["b"]))
    assert [t.trace_id for t in client(session, sleeps)._list(START, END)] == ["a", "b"]
    assert sleeps == [2.0] and session.params[2]["pageToken"] == "p2"


def step_json(tid, extra=()):
    spans = [{"name": "acme.scenario.step", "labels": {"acme.scenario": "s"}}, *extra]
    return {"traceId": tid, "spans": spans}


async def _proof(traces):
    from tests.live.agent_engine_proof import run_proof
    from tests.live.test_agent_engine_proof_logic import FakeBackend

    async def no_sleep(_):
        pass

    return await run_proof(
        FakeBackend(), FakeBackend(), traces, ("s",), project="p", pii=set(),
        sleep=no_sleep, now=lambda: START, monotonic=iter(range(0, 10_000, 50)).__next__,
        ingest_wait=10.0, settle_wait=10.0,
    )  # fmt: skip


async def test_a_late_turn_1_trace_on_page_2_is_matched_by_the_proof():
    from tests.live.test_agent_engine_proof_logic import KINDS

    older = step_json("t1", [{"name": k.name} for k in KINDS])  # only on page 2
    pages = [Response(200, {"traces": [step_json("t2")], "nextPageToken": "p2"}),
             Response(200, {"traces": [older]})] * 10  # fmt: skip
    traces = CloudTraceV1("p", session=Session(*pages), sleep=lambda _: None)
    r = await _proof(traces)
    assert {x.trace_id for x in r.rows} == {"t1", "t2"}
    assert r.failures == [] and traces.pages_fetched >= 2


async def test_after_settle_failure_reports_pages_fetched_and_cap_status():
    pages = [Response(200, {"traces": [step_json("t1")]})] * 20  # no a2a etc: kinds missing
    traces = CloudTraceV1("p", session=Session(*pages), sleep=lambda _: None)
    r = await _proof(traces)
    assert any("pages fetched, page cap hit: no" in f for f in r.failures)
