"""Hub step: list the project's agents over the hub REST API -> ``agent_snapshots`` rows.

``GET {HUB_ENDPOINT}/api/v1/projects/{HUB_PROJECT}/agents`` with ``Authorization: Bearer``
returns ``{"agents": [...], "totalCount": n}``. The path takes the project UUID, not the slug.
The token is never logged or put in error messages.
"""

import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

TIMEOUT = 10.0
ATTEMPTS = 3


class HubError(RuntimeError):
    pass


def fetch_agents(
    client: httpx.Client,
    project: str,
    token: str,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Return the raw agent dicts. Retries 5xx/transport errors; 4xx fails at once."""
    url = f"/api/v1/projects/{project}/agents"
    reason = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        except httpx.TransportError as e:
            reason = f"hub unreachable ({type(e).__name__})"
        else:
            if resp.status_code < 300:
                try:
                    agents = resp.json()["agents"]
                except (ValueError, KeyError, TypeError) as e:
                    raise HubError("hub returned an unexpected response body") from e
                if not isinstance(agents, list):
                    raise HubError("hub returned an unexpected response body")
                return agents
            reason = f"hub returned HTTP {resp.status_code}"
            if resp.status_code < 500:
                raise HubError(reason)
        log.warning("%s (attempt %d/%d)", reason, attempt, ATTEMPTS)
        if attempt < ATTEMPTS:
            sleep(2.0 * attempt)
    raise HubError(reason)


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def resolve_project(client: httpx.Client, project: str, token: str) -> str:
    """HUB_PROJECT may be the project UUID (used as is) or a slug (looked up once per run)."""
    if UUID_RE.match(project):
        return project
    hint = "set HUB_PROJECT to the project UUID (GET /api/v1/projects/<uuid> on the hub)"
    try:
        resp = client.get(
            "/api/v1/projects",
            params={"slug": project},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
    except httpx.TransportError as e:
        raise HubError(f"hub unreachable resolving HUB_PROJECT ({type(e).__name__})") from e
    if resp.status_code >= 300:
        raise HubError(
            f"cannot resolve HUB_PROJECT slug {project!r}: HTTP {resp.status_code}; {hint}"
        )
    try:
        matches = [p["id"] for p in resp.json()["projects"] if p.get("slug") == project]
    except (ValueError, KeyError, TypeError) as e:
        raise HubError("hub returned an unexpected project lookup body") from e
    if not matches:
        raise HubError(f"cannot resolve HUB_PROJECT slug {project!r} to a project; {hint}")
    return matches[0]


def map_agent(a: dict, ts: str) -> dict:
    labels = a.get("labels") or {}
    template = a.get("template") or ""
    activity = a.get("activity")
    return {
        "ts": ts,
        "name": a["name"],
        "template": template or None,
        "role": template.removeprefix("acme-") or None,
        "tier": labels.get("acme.team/tier") or "standing",
        "phase": a.get("phase"),
        "activity": activity,
        "container_status": a.get("containerStatus"),
        "started_at": a.get("startedAt"),
        "last_activity": a.get("lastActivityEvent"),
        "harness": a.get("harnessConfig"),
        "model": (a.get("appliedConfig") or {}).get("model"),
        "limits_exceeded": str(activity).lower() == "limits_exceeded",
    }


def rows_from(agents: list[dict], ts: str) -> list[dict]:
    return [map_agent(a, ts) for a in agents]


def hub_source(
    client: httpx.Client,
    project: str,
    token: str,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[str], list[dict]]:
    resolved: list[str] = []

    def run(ts: str) -> list[dict]:
        if not resolved:
            resolved.append(resolve_project(client, project, token))
        return rows_from(fetch_agents(client, resolved[0], token, sleep), ts)

    return run


def live_hub_source() -> Callable[[str], list[dict]]:
    """Source reading HUB_ENDPOINT / HUB_PROJECT / SCION_HUB_TOKEN when called."""

    def run(ts: str) -> list[dict]:
        env = {k: os.environ.get(k) for k in ("HUB_ENDPOINT", "HUB_PROJECT", "SCION_HUB_TOKEN")}
        missing = [k for k, v in env.items() if not v]
        if missing:
            raise HubError(f"missing environment: {', '.join(missing)}")
        with httpx.Client(base_url=env["HUB_ENDPOINT"]) as client:
            return hub_source(client, env["HUB_PROJECT"], env["SCION_HUB_TOKEN"])(ts)

    return run


def fixture_hub_source(path: Path) -> Callable[[str], list[dict]]:
    return lambda ts: rows_from(json.loads(path.read_text())["agents"], ts)
