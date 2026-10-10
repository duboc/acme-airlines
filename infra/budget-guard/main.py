"""acme-budget-guard: stop the factory VM when the monthly budget is exhausted.

Cloud Billing budget -> Pub/Sub topic -> push subscription (OIDC) -> this Cloud
Run service. When the notification's actual cost reaches the budget amount, it
stops the hub VM. All hub state, agent sessions and the standing roster live on
the VM disk, so `infra/60-factory.sh start-vm` resumes everything.

Standard library only. Config via env: PROJECT_ID, ZONE, INSTANCE.
"""
import base64
import json
import logging
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("budget-guard")

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)


def parse_notification(envelope: dict) -> dict:
    """Decode a Pub/Sub push envelope into the budget notification dict ({} if malformed)."""
    try:
        data = envelope["message"]["data"]
        return json.loads(base64.b64decode(data, validate=True).decode())
    except (KeyError, TypeError, ValueError):
        return {}


def should_stop(notification: dict) -> bool:
    """Stop only on ACTUAL cost >= budget (never on forecasts, zero or missing budgets)."""
    try:
        cost = float(notification.get("costAmount", 0))
        budget = float(notification.get("budgetAmount", 0))
    except (TypeError, ValueError):
        return False
    return budget > 0 and cost >= budget


def _token() -> str:
    req = urllib.request.Request(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)["access_token"]


def _compute(method: str, path: str) -> dict:
    project, zone, inst = os.environ["PROJECT_ID"], os.environ["ZONE"], os.environ["INSTANCE"]
    url = f"https://compute.googleapis.com/compute/v1/projects/{project}/zones/{zone}/instances/{inst}{path}"
    req = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {_token()}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def stop_vm_if_running() -> str:
    status = _compute("GET", "").get("status", "UNKNOWN")
    if status != "RUNNING":
        return f"vm already {status}"
    _compute("POST", "/stop")
    return "vm stop requested"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        try:
            envelope = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            envelope = {}
        n = parse_notification(envelope)
        result = "no action"
        if should_stop(n):
            try:
                result = stop_vm_if_running()
            except (urllib.error.URLError, KeyError) as e:
                # Return 500 so Pub/Sub retries.
                log.error("stop failed: %s", e)
                self.send_response(500)
                self.end_headers()
                return
        log.info("cost=%s budget=%s -> %s", n.get("costAmount"), n.get("budgetAmount"), result)
        self.send_response(204)
        self.end_headers()

    def do_GET(self):  # noqa: N802 — health
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # silence default access log
        pass


if __name__ == "__main__":
    HTTPServer(("", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
