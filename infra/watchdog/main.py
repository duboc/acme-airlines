"""acme-factory-watchdog: wake-on-change, self-healing and daily reincarnation for acme-lead.

Cloud Scheduler (OIDC POST) -> this Cloud Run service -> (Direct VPC egress) hub at
HUB_ENDPOINT using a project-scoped UAT (agent:list, agent:read, agent:message,
agent:lifecycle) from Secret Manager, plus GitHub REST API with GITHUB_TOKEN.

Modes (JSON body {"mode": "..."} or request path):
  - check (every hour): fingerprint GitHub + worker phases; wake acme-lead only when
    idle and state changed (rate-limited to 1 wake / 55 min); recover a stalled/errored
    lead (message/resume first, reincarnate + alert PO if still unhealthy on next hourly check);
    NEVER resume a lead in phase 'suspended' (intentional pause by guard or operator).
  - reincarnate (daily 04:00 UTC): reincarnate acme-lead with a handoff built from the
    pinned '🏭 Factory status' issue and current child-agent roster, resetting multi-day
    Opus context growth while keeping agent ID, slug and lineage intact.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

STATUS_TITLE = "🏭 Factory status"
DEFAULT_STATE_PATH = Path("/tmp/watchdog-state.json")
IDLE_ACTIVITIES = {"", "blocked", "waiting_for_input", "completed"}
BUSY_ACTIVITIES = {"working", "thinking", "executing"}
UNHEALTHY_ACTIVITIES = {"stalled", "crashed", "offline", "limits_exceeded"}
UNHEALTHY_PHASES = {"error", "stopped"}


def log_json(severity: str, **fields) -> None:
    rec = {"severity": severity, "component": "acme-factory-watchdog", **fields}
    print(json.dumps(rec, sort_keys=True), flush=True)


def _issue_labels(issue: dict) -> list[str]:
    return sorted(
        lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
        for lbl in (issue.get("labels") or [])
    )


def compute_fingerprint(issues: list[dict], prs: list[dict], runs: list[dict], agents: list[dict]) -> str:
    norm_issues = sorted(
        (
            int(i.get("number", 0)),
            str(i.get("updated_at", "")),
            _issue_labels(i),
        )
        for i in issues
        if i.get("title") != STATUS_TITLE
    )
    norm_prs = sorted(
        (
            int(p.get("number", 0)),
            str((p.get("head") or {}).get("sha", "")),
            str(p.get("updated_at", "")),
        )
        for p in prs
    )
    norm_runs = [
        (
            str(r.get("name", "")),
            str(r.get("head_sha", "")),
            str(r.get("status", "")),
            str(r.get("conclusion", "")),
        )
        for r in runs[:10]
    ]
    norm_agents = sorted(
        (str(a.get("slug") or a.get("name", "")), str(a.get("phase", "")).lower(), str(a.get("activity", "")).lower())
        for a in agents
        if (a.get("slug") or a.get("name")) not in ("lead", "acme-lead", "eval-lead")
    )
    payload = json.dumps(
        {"issues": norm_issues, "prs": norm_prs, "runs": norm_runs, "agents": norm_agents},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_eval_fingerprint(issues: list[dict], runs: list[dict], agents: list[dict]) -> str:
    eval_issues = []
    for i in issues:
        if i.get("title") == STATUS_TITLE:
            continue
        lbls = _issue_labels(i)
        if "status:in-review" in lbls or "acme-airlines-eval" in lbls or "area:eval" in lbls:
            eval_issues.append((int(i.get("number", 0)), str(i.get("updated_at", "")), lbls))
    eval_issues.sort()
    norm_runs = [
        (
            str(r.get("name", "")),
            str(r.get("head_sha", "")),
            str(r.get("status", "")),
            str(r.get("conclusion", "")),
        )
        for r in runs[:10]
    ]
    norm_agents = sorted(
        (str(a.get("slug") or a.get("name", "")), str(a.get("phase", "")).lower(), str(a.get("activity", "")).lower())
        for a in agents
        if (a.get("slug") or a.get("name")) not in ("lead", "acme-lead", "eval-lead")
    )
    payload = json.dumps(
        {"issues": eval_issues, "runs": norm_runs, "agents": norm_agents},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def decide_action(
    lead: dict | None,
    fp: str,
    state: dict,
    now: float,
    min_wake_interval: int = 840,
    stall_escalate_sec: int = 3300,
    max_wakes_before_reincarnate: int = 12,
    max_turns_before_reincarnate: int = 40,
    max_model_calls_before_reincarnate: int = 120,
) -> dict:
    new_state = dict(state)
    if lead is None:
        return {"action": "noop", "reason": "acme-lead not found", "alert_po": True, "new_state": new_state}

    phase = str(lead.get("phase", "")).lower()
    activity = str(lead.get("activity", "")).lower()

    # Never resume an intentionally paused factory (budget guard or `60-factory.sh pause`).
    if phase == "suspended":
        new_state["unhealthy_since"] = None
        return {
            "action": "noop",
            "reason": "factory paused (lead suspended)",
            "alert_po": False,
            "new_state": new_state,
        }

    if phase in UNHEALTHY_PHASES or activity in UNHEALTHY_ACTIVITIES:
        since = state.get("unhealthy_since")
        want_act = "recover_start" if phase in UNHEALTHY_PHASES else "recover_message"
        prev_act = state.get("last_recovery_action")
        if since is None or (want_act == "recover_start" and prev_act != "recover_start"):
            new_state["unhealthy_since"] = now
            new_state["last_recovery_action"] = want_act
            return {
                "action": want_act,
                "reason": f"lead unhealthy (phase={phase} activity={activity})",
                "alert_po": False,
                "new_state": new_state,
            }
        if now - float(since) >= stall_escalate_sec:
            new_state["unhealthy_since"] = now
            new_state["last_recovery_action"] = "reincarnate"
            new_state["wakes_since_reincarnate"] = 0
            return {
                "action": "reincarnate",
                "reason": f"lead still unhealthy after {int(now - float(since))}s (phase={phase} activity={activity})",
                "alert_po": True,
                "new_state": new_state,
            }
        return {
            "action": "noop",
            "reason": f"waiting for first recovery ({int(now - float(since))}s elapsed)",
            "alert_po": False,
            "new_state": new_state,
        }

    new_state["unhealthy_since"] = None
    new_state.pop("last_recovery_action", None)

    if phase != "running" or activity in BUSY_ACTIVITIES or activity not in IDLE_ACTIVITIES:
        return {
            "action": "noop",
            "reason": f"lead busy (phase={phase} activity={activity})",
            "alert_po": False,
            "new_state": new_state,
        }

    turns = int(lead.get("current_turns") or 0)
    model_calls = int(lead.get("current_model_calls") or 0)
    if turns >= max_turns_before_reincarnate or model_calls >= max_model_calls_before_reincarnate:
        new_state["fingerprint"] = fp
        new_state["last_wake_ts"] = now
        new_state["wakes_since_reincarnate"] = 0
        return {
            "action": "reincarnate",
            "reason": f"context compaction threshold reached (turns={turns} model_calls={model_calls})",
            "alert_po": False,
            "new_state": new_state,
        }

    if fp == state.get("fingerprint"):
        return {"action": "noop", "reason": "no change", "alert_po": False, "new_state": new_state}

    last_wake = float(state.get("last_wake_ts") or 0)
    if now - last_wake < min_wake_interval:
        return {
            "action": "noop",
            "reason": f"change detected, rate-limited ({int(now - last_wake)}s < {min_wake_interval}s)",
            "alert_po": False,
            "new_state": new_state,
        }

    wakes = int(state.get("wakes_since_reincarnate") or 0) + 1
    new_state["fingerprint"] = fp
    new_state["last_wake_ts"] = now
    if wakes >= max_wakes_before_reincarnate:
        new_state["wakes_since_reincarnate"] = 0
        return {
            "action": "reincarnate",
            "reason": f"context compaction threshold reached ({wakes} wakes)",
            "alert_po": False,
            "new_state": new_state,
        }

    new_state["wakes_since_reincarnate"] = wakes
    return {"action": "wake", "reason": "state changed", "alert_po": False, "new_state": new_state}


def build_handoff(
    status_issue_body: str,
    agents: list[dict],
    now_iso: str,
    lead_name: str = "lead",
) -> str:
    roster_lines = [
        f"- {a.get('slug') or a.get('name')}: phase={a.get('phase')} activity={a.get('activity') or '-'}"
        for a in sorted(agents, key=lambda x: str(x.get("slug") or x.get("name") or ""))
        if (a.get("slug") or a.get("name")) not in ("lead", "acme-lead", "eval-lead")
    ] or ["- (none listed)"]
    if lead_name == "eval-lead":
        return f"""# Reincarnation handoff

Prepared at {now_iso} by `acme-factory-watchdog` from the durable GitHub state.

## Role charter

You are `eval-lead`, the evaluation & quality orchestrator of the `acme-airlines-eval` project (`duboc/acme-airlines`).
You coordinate your standing evaluation roster (`eval-heartbeat`, `search-evaluator`, `factory-analyst`, `ui-craftsman`, `eval-deployer`), audit deployed `status:in-review` issues against real application & GenAI evaluation criteria, close approved issues or bounce regressions back with evidence, and keep the evaluation observatory dashboard updated.

## Immediate active work (status, next action)

Run one evaluation loop pass now (`scion list`, `gh issue list --label status:in-review`, `gh issue list --label area:eval`), resume any stopped workers, dispatch pending evaluations, then wait with
`sciontool status blocked "waiting: <what, from whom>"`.

## Children agents and their state

{chr(10).join(roster_lines)}

## Pending waits and scheduled events

- `eval-heartbeat` runs once per day (`0 12 * * *` UTC) on `Gemini 3.8 Flash (Medium)` and messages you only when `status:in-review` or `acme-airlines-eval` work is actionable.
- `acme-factory-watchdog` runs once per day (`0 12 * * *` UTC) with zero idle LLM cost and wakes you only when `status:in-review` / `area:eval` issues, runs, or worker states change.
"""
    raw_status = status_issue_body.strip()
    if len(raw_status) > 800:
        status_snippet = raw_status[:800] + "\n...(truncated to fit tmux command buffer; check `🏭 Factory status` issue on GitHub for full body)"
    else:
        status_snippet = raw_status or "(status issue body was empty — check `gh issue list --search 'Factory status'`)"
    return f"""# Reincarnation handoff

Prepared at {now_iso} by `acme-factory-watchdog` from the durable GitHub status issue.

## Role charter

You are `lead`, the tech-lead orchestrator of the `duboc/acme-airlines` software factory.
You plan, triage, delegate in parallel across your standing roster (`heartbeat`, `researcher`, `product-manager`, `backend`, `frontend`, `reviewer`, `deployer`), verify reviews and CI, merge PRs, and hand off deployed issues as `status:in-review` for `eval-lead` to approve and close.
You do not write feature code.

## Immediate active work (status, next action)

Run one factory loop pass now (`scion list`, `gh issue list`, `gh pr list`), resume any stopped workers, reconcile with the
latest status snapshot below, then wait with
`sciontool status blocked "waiting: <what, from whom>"`.

Latest `🏭 Factory status` snapshot:

{status_snippet}

## Canonical files and artifacts

- `AGENTS.md` and your system instructions (`docs/scion/templates/lead/agents.md`, `docs/scion/templates/lead/system-prompt.md`)
- Product brief & docs: `docs/product/irrops-use-case.md`, `docs/product/engines.md`, `docs/product/deploy.md`
- Pinned status issue: `🏭 Factory status` on `duboc/acme-airlines`

## Authority and ownership (who to ask, who can approve)

- Product owner: configured Hub admin (`ADMIN_EMAIL` / GitHub `duboc`). Contact the PO ONLY when genuinely stuck after 2 recovery attempts on an external IAM grant or irreconcilable scope conflict.
- You merge PRs autonomously once the latest review has `VERDICT: APPROVE` and required CI checks pass.
- Act only on issues, PRs and comments authored by `duboc` (the repo is public).

## Live conversations (conv ids) and counterparties

All task state lives on GitHub Issues and PRs; check open `status:in-progress` and `status:in-review` issues.

## Children agents and their state

{chr(10).join(roster_lines)}

## Pending waits and scheduled events

- `heartbeat` runs once per day (`0 12 * * *` UTC) on `Gemini 3.8 Flash (Medium)` and messages you only when open issues or PRs are actionable.
- `acme-factory-watchdog` runs once per day (`0 12 * * *` UTC) with zero idle LLM cost and wakes you only when GitHub or worker states change.

## Open questions to humans (already asked and not yet asked)

See `status:blocked` issues on GitHub.

## Operating constraints and lessons learned

- Never delete standing agents (`researcher`, `product-manager`, `backend`, `frontend`, `reviewer`, `deployer`); if any standing worker is `stopped` or `error`, immediately resume it with `scion resume <worker>`.
- Vertex AI calls use `global` only; Agent Engine control plane is `us-central1` and injects `GOOGLE_CLOUD_PROJECT` as the project number (use `ACME_GENAI_PROJECT` / `ACME_GENAI_LOCATION`).

## Do not redo

Do not re-create the standing roster or the pinned `🏭 Factory status` issue; both already exist.
"""


def _scion_env() -> dict[str, str]:
    env = dict(os.environ)
    env["SCION_HUB_ENDPOINT"] = os.environ["HUB_ENDPOINT"]
    env["SCION_HUB_TOKEN"] = os.environ["SCION_HUB_TOKEN"]
    return env


def execute_action(
    action: str,
    reason: str,
    handoff_text: str = "",
    run=subprocess.run,
    tmp_dir: Path = Path("/tmp"),
    lead_name: str = "acme-lead",
    project: str | None = None,
) -> str:
    if action == "noop":
        return "noop"
    proj = project or os.environ["HUB_PROJECT"]
    env = _scion_env()
    if action == "wake":
        msg = f"WAKE ({reason}): GitHub or worker state changed. Run one factory loop pass."
        args = ["scion", "message", lead_name, "--project", proj, msg]
    elif action == "recover_message":
        msg = (
            f"WATCHDOG ({reason}): you appear stalled. Resume your loop pass, "
            "do not suspend standing agents, and finish with sciontool status blocked."
        )
        args = ["scion", "message", lead_name, "--project", proj, msg]
    elif action == "recover_start":
        args = ["scion", "resume", lead_name, "--project", proj, "WATCHDOG: recovered from stopped/error phase. Run one factory loop pass."]
    elif action == "reincarnate":
        hf = tmp_dir / f"{lead_name}-handoff.md"
        hf.write_text(handoff_text or "# Reincarnation handoff\nRun one factory loop pass.\n")
        args = ["scion", "reincarnate", lead_name, "--project", proj, "--handoff-file", str(hf)]
    else:
        raise ValueError(f"unknown action: {action}")

    r = run(args, env=env, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed rc={r.returncode}: {(r.stderr or r.stdout or '')[:300]}")
    return (r.stdout or "").strip()[:300] or action


def fetch_hub_agents(run=subprocess.run, project: str | None = None) -> list[dict]:
    proj = project or os.environ["HUB_PROJECT"]
    cmd = ["scion", "list", "--project", proj, "--format", "json"]
    try:
        r = run(cmd, env=_scion_env(), capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        r = run(cmd, env=_scion_env(), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"scion list ({proj}) failed rc={r.returncode}: {(r.stderr or '')[:300]}")
    data = json.loads(r.stdout or "[]")
    return data if isinstance(data, list) else (data.get("agents") or [])


def _gh_get(path: str, token: str):
    req = urllib.request.Request(
        f"https://api.github.com/{path.lstrip('/')}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "acme-factory-watchdog",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_github_state(repo: str, token: str) -> tuple[list[dict], list[dict], list[dict], str]:
    raw_issues = _gh_get(f"repos/{repo}/issues?state=open&per_page=100", token)
    issues = [i for i in raw_issues if "pull_request" not in i]
    prs = _gh_get(f"repos/{repo}/pulls?state=open&per_page=50", token)
    runs_resp = _gh_get(f"repos/{repo}/actions/runs?branch=main&per_page=10", token)
    runs = runs_resp.get("workflow_runs") or []
    status_body = next((str(i.get("body") or "") for i in issues if i.get("title") == STATUS_TITLE), "")
    return issues, prs, runs, status_body


def _gcp_token() -> str:
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())["access_token"]


def load_state(local_path: Path | None = None) -> dict:
    local_path = local_path or DEFAULT_STATE_PATH
    bucket = os.environ.get("STATE_BUCKET", "")
    if bucket:
        try:
            url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/watchdog%2Fstate.json?alt=media"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_gcp_token()}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            pass
    if local_path.exists():
        try:
            return json.loads(local_path.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict, local_path: Path | None = None) -> None:
    local_path = local_path or DEFAULT_STATE_PATH
    raw = json.dumps(state, sort_keys=True).encode()
    local_path.write_bytes(raw)
    bucket = os.environ.get("STATE_BUCKET", "")
    if bucket:
        try:
            url = f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o?uploadType=media&name=watchdog/state.json"
            req = urllib.request.Request(
                url,
                data=raw,
                method="POST",
                headers={"Authorization": f"Bearer {_gcp_token()}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15):
                pass
        except Exception as e:
            log_json("WARNING", message=f"failed to persist state to GCS: {e}", alert_po=False)


def run_once(mode: str) -> tuple[int, dict]:
    repo = os.environ["GITHUB_REPO"]
    gh_token = os.environ["GITHUB_TOKEN"].strip()
    hub_project = os.environ["HUB_PROJECT"]
    eval_project = os.environ.get("EVAL_PROJECT", "").strip()
    agents = fetch_hub_agents(project=hub_project)
    lead = next((a for a in agents if (a.get("slug") or a.get("name")) in ("lead", "acme-lead")), None)
    lead_name = str((lead or {}).get("slug") or (lead or {}).get("name") or "lead")
    issues, prs, runs, status_body = fetch_github_state(repo, gh_token)
    now = time.time()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    handoff = build_handoff(status_body, agents, now_iso, lead_name=lead_name)

    if mode == "reincarnate":
        if lead is not None and str(lead.get("phase", "")).lower() == "suspended":
            res = {"mode": mode, "action": "noop", "reason": "factory paused (lead suspended)", "alert_po": False}
            log_json("INFO", **res)
            return 200, res
        out = execute_action(
            "reincarnate",
            "daily scheduled reincarnation",
            handoff_text=handoff,
            lead_name=lead_name,
            project=hub_project,
        )
        state = load_state()
        state["wakes_since_reincarnate"] = 0
        state["wake_count"] = 0
        eval_out = None
        if eval_project:
            try:
                eval_agents = fetch_hub_agents(project=eval_project)
                eval_lead = next(
                    (a for a in eval_agents if (a.get("slug") or a.get("name")) in ("eval-lead", "lead")),
                    None,
                )
                if eval_lead is not None and str(eval_lead.get("phase", "")).lower() != "suspended":
                    eval_lead_name = str(eval_lead.get("slug") or eval_lead.get("name") or "eval-lead")
                    eval_handoff = build_handoff(status_body, eval_agents, now_iso, lead_name=eval_lead_name)
                    eval_out = execute_action(
                        "reincarnate",
                        "daily scheduled reincarnation",
                        handoff_text=eval_handoff,
                        lead_name=eval_lead_name,
                        project=eval_project,
                    )
                    if isinstance(state.get("eval"), dict):
                        state["eval"]["wakes_since_reincarnate"] = 0
                        state["eval"]["wake_count"] = 0
            except Exception as e:
                log_json("WARNING", message=f"eval-lead daily reincarnation failed: {e}", alert_po=False)
        save_state(state)
        res = {
            "mode": mode,
            "action": "reincarnate",
            "reason": "daily scheduled reincarnation",
            "alert_po": False,
            "output": out,
        }
        if eval_out is not None:
            res["eval_output"] = eval_out
        log_json("INFO", **res)
        return 200, res

    min_wake = int(os.environ.get("MIN_WAKE_INTERVAL_SEC", "840"))
    stall_sec = int(os.environ.get("STALL_ESCALATE_SEC", "3300"))
    max_wakes = int(os.environ.get("MAX_WAKES_BEFORE_REINCARNATE", "12"))
    max_turns = int(os.environ.get("MAX_TURNS_BEFORE_REINCARNATE", "40"))
    max_model_calls = int(os.environ.get("MAX_MODEL_CALLS_BEFORE_REINCARNATE", "120"))

    fp = compute_fingerprint(issues, prs, runs, agents)
    state = load_state()
    decision = decide_action(
        lead,
        fp,
        state,
        now=now,
        min_wake_interval=min_wake,
        stall_escalate_sec=stall_sec,
        max_wakes_before_reincarnate=max_wakes,
        max_turns_before_reincarnate=max_turns,
        max_model_calls_before_reincarnate=max_model_calls,
    )
    out = execute_action(
        decision["action"],
        decision["reason"],
        handoff_text=handoff,
        lead_name=lead_name,
        project=hub_project,
    )
    combined_state = dict(decision["new_state"])
    res = {
        "mode": mode,
        "action": decision["action"],
        "reason": decision["reason"],
        "alert_po": decision["alert_po"],
        "fingerprint": fp[:12],
        "lead_phase": (lead or {}).get("phase"),
        "lead_activity": (lead or {}).get("activity"),
        "output": out,
    }

    if eval_project:
        try:
            eval_agents = fetch_hub_agents(project=eval_project)
            eval_lead = next(
                (a for a in eval_agents if (a.get("slug") or a.get("name")) in ("eval-lead", "lead")),
                None,
            )
            eval_lead_name = str((eval_lead or {}).get("slug") or (eval_lead or {}).get("name") or "eval-lead")
            eval_fp = compute_eval_fingerprint(issues, runs, eval_agents)
            eval_prev_state = state.get("eval") if isinstance(state.get("eval"), dict) else {}
            eval_decision = decide_action(
                eval_lead,
                eval_fp,
                eval_prev_state,
                now=now,
                min_wake_interval=min_wake,
                stall_escalate_sec=stall_sec,
                max_wakes_before_reincarnate=max_wakes,
                max_turns_before_reincarnate=max_turns,
                max_model_calls_before_reincarnate=max_model_calls,
            )
            eval_handoff = build_handoff(status_body, eval_agents, now_iso, lead_name=eval_lead_name)
            eval_out = execute_action(
                eval_decision["action"],
                eval_decision["reason"],
                handoff_text=eval_handoff,
                lead_name=eval_lead_name,
                project=eval_project,
            )
            combined_state["eval"] = eval_decision["new_state"]
            res["eval"] = {
                "project": eval_project,
                "action": eval_decision["action"],
                "reason": eval_decision["reason"],
                "alert_po": eval_decision["alert_po"],
                "fingerprint": eval_fp[:12],
                "lead_phase": (eval_lead or {}).get("phase"),
                "lead_activity": (eval_lead or {}).get("activity"),
                "output": eval_out,
            }
            if eval_decision["alert_po"]:
                res["alert_po"] = True
        except Exception as e:
            log_json("WARNING", message=f"eval watchdog pass failed: {e}", alert_po=False)
            res["eval"] = {"project": eval_project, "action": "error", "reason": str(e)}

    save_state(combined_state)
    sev = "ERROR" if res["alert_po"] else "INFO"
    log_json(sev, **res)
    return 200, res


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        mode = "reincarnate" if self.path.rstrip("/").endswith("/reincarnate") else str(body.get("mode") or "check")
        try:
            code, payload = run_once(mode)
        except Exception as e:
            log_json("ERROR", mode=mode, action="error", reason=str(e), alert_po=True)
            code, payload = 500, {"error": str(e)}
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("check", "reincarnate"):
        _, out = run_once(sys.argv[1])
        sys.exit(0)
    HTTPServer(("", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
