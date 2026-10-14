import logging

import httpx
import pytest

from collector.github import GitHubError, fixture_client, github_source, live_github_source
from collector.schema import GITHUB_SNAPSHOTS, validate_row

TOKEN = "ghp_s3cret"
TS = "2026-10-10T12:00:00+00:00"


def snapshot():
    return github_source(fixture_client(), "o/r", None)(TS)


def test_fixture_row_is_valid_and_counts_are_right():
    (row,) = snapshot()
    validate_row(GITHUB_SNAPSHOTS, row)
    assert row["ts"] == TS
    status = {r["status"]: r["count"] for r in row["issues_by_status"]}
    assert status == {"ready": 2, "in-progress": 1, "in-review": 1}
    labels = {r["label"]: r["count"] for r in row["issues_by_label"]}
    assert labels == {
        "role:backend": 2,
        "priority:p1": 1,
        "area:observatory": 1,
        "role:frontend": 1,
    }
    assert row["merges_24h"] == 1
    assert {r["conclusion"]: r["count"] for r in row["ci_conclusions"]} == {
        "success": 3,
        "failure": 1,
        "other": 2,
    }


def test_latest_verdict_wins_across_reviews_and_comments_and_ci_per_pr():
    prs = {p["number"]: p for p in snapshot()[0]["open_prs"]}
    assert prs[301]["latest_verdict"] == "APPROVE"  # later review beats earlier REQUEST CHANGES
    assert prs[302]["latest_verdict"] == "REQUEST CHANGES"  # later comment beats review
    assert prs[303]["latest_verdict"] is None
    assert [prs[n]["ci_conclusion"] for n in (301, 302, 303)] == ["success", "failure", "pending"]
    assert prs[301]["author"] == "duboc"


def test_no_token_warns_and_skips(monkeypatch, caplog):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING):
        assert live_github_source()(TS) == []
    assert "GitHub snapshot skipped" in caplog.text


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_github_failure_warns_and_returns_nothing(monkeypatch, caplog, status):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPO", "o/r")
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json={"message": TOKEN}))
    with caplog.at_level(logging.DEBUG):
        assert live_github_source(transport=transport)(TS) == []
    assert "GitHub snapshot skipped" in caplog.text
    assert TOKEN not in caplog.text


def test_github_error_raised_by_source_directly():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(403)), base_url="https://x"
    )
    with pytest.raises(GitHubError, match="403"):
        github_source(client, "o/r", TOKEN)(TS)


def test_sends_bearer_and_paginates(monkeypatch):
    pages = []

    def handler(r):
        assert r.headers["authorization"] == f"Bearer {TOKEN}"
        if r.url.path.endswith("/issues"):
            page = int(r.url.params.get("page", "1"))
            pages.append(page)
            n = 100 if page == 1 else 1
            return httpx.Response(200, json=[{"number": i, "labels": []} for i in range(n)])
        if r.url.path.endswith("/actions/runs"):
            return httpx.Response(200, json={"workflow_runs": []})
        return httpx.Response(200, json=[])

    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPO", "o/r")
    live_github_source(transport=httpx.MockTransport(handler))(TS)
    assert pages == [1, 2]
