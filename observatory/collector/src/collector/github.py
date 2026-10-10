"""GitHub step: one ``github_snapshots`` row from the repo's issues, PRs and workflow runs.

Read-only REST calls with ``GITHUB_TOKEN``. Any GitHub failure, or a missing token, logs a
``GitHub snapshot skipped`` warning and yields no row, so it never blocks the hub snapshot.
The token is never logged.
"""

import json
import logging
import os
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

TIMEOUT = 15.0
PER_PAGE = 100
MAX_PAGES = 5
FIXTURE_DIR = Path(
    os.environ.get("OBSERVATORY_FIXTURES") or Path(__file__).resolve().parents[3] / "fixtures"
)


class GitHubError(RuntimeError):
    pass


def _get(client: httpx.Client, path: str, params: dict | None = None):
    try:
        resp = client.get(path, params=params, timeout=TIMEOUT)
    except httpx.TransportError as e:
        raise GitHubError(f"GitHub unreachable ({type(e).__name__})") from e
    if resp.status_code >= 300:
        raise GitHubError(f"GitHub returned HTTP {resp.status_code} for {path}")
    try:
        return resp.json()
    except ValueError as e:
        raise GitHubError(f"GitHub returned invalid JSON for {path}") from e


def _pages(client, path, params, key=None, stop: Callable[[list], bool] = lambda items: False):
    out: list = []
    for page in range(1, MAX_PAGES + 1):
        body = _get(client, path, {**params, "per_page": PER_PAGE, "page": page})
        items = body[key] if key else body
        out.extend(items)
        if len(items) < PER_PAGE or stop(items):
            break
    return out


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _verdict(body: str | None) -> str | None:
    first = (body or "").strip().split("\n", 1)[0].strip()
    if not first.upper().startswith("VERDICT:"):
        return None
    value = first[len("VERDICT:") :].strip().upper()
    return value if value in ("APPROVE", "REQUEST CHANGES") else None


def _latest_verdict(client, repo: str, number: int) -> str | None:
    events = []
    for item in _pages(client, f"/repos/{repo}/pulls/{number}/reviews", {}):
        events.append((item.get("submitted_at") or "", item.get("body")))
    for item in _pages(client, f"/repos/{repo}/issues/{number}/comments", {}):
        events.append((item.get("created_at") or "", item.get("body")))
    verdicts = [(t, v) for t, b in events if (v := _verdict(b))]
    return max(verdicts)[1] if verdicts else None


def _ci_conclusion(runs: list[dict]) -> str | None:
    if not runs:
        return None
    if any(r.get("conclusion") == "failure" for r in runs):
        return "failure"
    if any(r.get("conclusion") is None for r in runs):
        return "pending"
    return "success" if all(r["conclusion"] == "success" for r in runs) else "failure"


def _counts(counter: Counter, key: str) -> list[dict]:
    return [{key: k, "count": n} for k, n in counter.items()]


def github_source(
    client: httpx.Client, repo: str, _token: str | None
) -> Callable[[str], list[dict]]:
    def run(ts: str) -> list[dict]:
        cutoff = _parse(ts) - timedelta(hours=24)
        by_status: Counter = Counter()
        by_label: Counter = Counter()
        for issue in _pages(client, f"/repos/{repo}/issues", {"state": "open"}):
            if "pull_request" in issue:
                continue
            for label in issue.get("labels", []):
                name = label["name"]
                if name.startswith("status:"):
                    by_status[name.removeprefix("status:")] += 1
                else:
                    by_label[name] += 1

        closed = _pages(
            client,
            f"/repos/{repo}/pulls",
            {"state": "closed", "sort": "updated", "direction": "desc"},
            stop=lambda items: _parse(items[-1]["updated_at"]) < cutoff,
        )
        merges = sum(1 for p in closed if p.get("merged_at") and _parse(p["merged_at"]) >= cutoff)

        runs = _pages(
            client,
            f"/repos/{repo}/actions/runs",
            {"created": f">={cutoff:%Y-%m-%dT%H:%M:%SZ}"},
            key="workflow_runs",
        )
        ci = Counter(
            r["conclusion"] if r.get("conclusion") in ("success", "failure") else "other"
            for r in runs
        )

        prs = []
        for pr in _pages(client, f"/repos/{repo}/pulls", {"state": "open"}):
            sha = pr["head"]["sha"]
            prs.append(
                {
                    "number": pr["number"],
                    "title": pr.get("title"),
                    "author": (pr.get("user") or {}).get("login"),
                    "created_at": pr.get("created_at"),
                    "latest_verdict": _latest_verdict(client, repo, pr["number"]),
                    "ci_conclusion": _ci_conclusion([r for r in runs if r.get("head_sha") == sha]),
                }
            )
        return [
            {
                "ts": ts,
                "issues_by_status": _counts(by_status, "status"),
                "issues_by_label": _counts(by_label, "label"),
                "open_prs": prs,
                "merges_24h": merges,
                "ci_conclusions": _counts(ci, "conclusion"),
            }
        ]

    return run


def fixture_client() -> httpx.Client:
    """A client answering from recorded responses in ``fixtures/github/`` (no network)."""
    root = FIXTURE_DIR / "github"

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")[3:]  # drop repos/<owner>/<repo>
        state = request.url.params.get("state")
        if parts == ["issues"]:
            name = "issues"
        elif parts == ["pulls"]:
            name = f"pulls_{state}"
        elif parts == ["actions", "runs"]:
            name = "runs"
        elif len(parts) == 3 and parts[0] in ("pulls", "issues"):
            name = f"{'reviews' if parts[0] == 'pulls' else 'comments'}_{parts[1]}"
        else:
            return httpx.Response(404)
        if request.url.params.get("page", "1") != "1":
            return httpx.Response(200, json={"workflow_runs": []} if name == "runs" else [])
        return httpx.Response(200, json=json.loads((root / f"{name}.json").read_text()))

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fixture.invalid")


def fixture_github_source() -> Callable[[str], list[dict]]:
    return github_source(fixture_client(), "fixture/repo", None)


def live_github_source(transport: httpx.BaseTransport | None = None) -> Callable[[str], list[dict]]:
    def run(ts: str) -> list[dict]:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            log.warning("GitHub snapshot skipped: GITHUB_TOKEN is not set")
            return []
        repo = os.environ.get("GITHUB_REPO", "duboc/acme-airlines")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            with httpx.Client(
                base_url="https://api.github.com", headers=headers, transport=transport
            ) as client:
                return github_source(client, repo, token)(ts)
        except GitHubError as e:
            log.warning("GitHub snapshot skipped: %s", e)
            return []

    return run
