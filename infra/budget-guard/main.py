"""acme-budget-guard: pause the agent factory when the monthly budget is exhausted.

Product-owner rule: NEVER stop the VM. The budget is mostly token spend, so at
>= 100% of ACTUAL spend the guard suspends every agent in the acme-airlines hub
project (`scion suspend --all`). Agent sessions, the standing roster and the
lineage graph are kept; the hub, UI and VM keep running. Resume with
`infra/60-factory.sh resume`.

Cloud Billing budget -> Pub/Sub -> authenticated push -> this Cloud Run service
-> (Direct VPC egress) hub at HUB_ENDPOINT, authenticated with a project-scoped
user access token (scopes agent:list, agent:lifecycle) from Secret Manager.

Config via env: HUB_ENDPOINT, HUB_PROJECT, SCION_HUB_TOKEN (secret).
"""
import base64
import json
import logging
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("budget-guard")


def parse_notification(envelope: dict) -> dict:
    """Decode a Pub/Sub push envelope into the budget notification dict ({} if malformed)."""
    try:
        data = envelope["message"]["data"]
        return json.loads(base64.b64decode(data, validate=True).decode())
    except (KeyError, TypeError, ValueError):
        return {}


def should_pause(notification: dict) -> bool:
    """Pause only on ACTUAL cost >= budget (never on forecasts, zero or missing budgets)."""
    try:
        cost = float(notification.get("costAmount", 0))
        budget = float(notification.get("budgetAmount", 0))
    except (TypeError, ValueError):
        return False
    return budget > 0 and cost >= budget


def pause_factory(run=subprocess.run) -> str:
    """Suspend every agent in the hub project (and eval project if configured). Raises RuntimeError on failure."""
    env = dict(os.environ)
    env["SCION_HUB_ENDPOINT"] = os.environ["HUB_ENDPOINT"]
    env["SCION_HUB_TOKEN"] = os.environ["SCION_HUB_TOKEN"]
    projects = [os.environ["HUB_PROJECT"]]
    eval_proj = os.environ.get("EVAL_PROJECT", "").strip()
    if eval_proj and eval_proj not in projects:
        projects.append(eval_proj)
    for proj in projects:
        args = ["scion", "suspend", "--all", "--project", proj]
        r = run(args, env=env, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"scion suspend ({proj}) failed rc={r.returncode}: {(r.stderr or '')[:300]}")
        log.info("suspend (%s) output: %s", proj, (r.stdout or "")[:500])
    return "agents suspended"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        try:
            envelope = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            envelope = {}
        n = parse_notification(envelope)
        result = "no action"
        if should_pause(n):
            if os.environ.get("PAUSE_ON_BUDGET", "0") == "1":
                try:
                    result = pause_factory()
                except (RuntimeError, KeyError, subprocess.SubprocessError, OSError) as e:
                    log.error("pause failed: %s", e)
                    self.send_response(500)  # Pub/Sub retries
                    self.end_headers()
                    return
            else:
                result = "visibility alert only (PAUSE_ON_BUDGET=0)"
        log.info("cost=%s budget=%s -> %s", n.get("costAmount"), n.get("budgetAmount"), result)
        self.send_response(204)
        self.end_headers()

    def do_GET(self):  # noqa: N802 — health
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
