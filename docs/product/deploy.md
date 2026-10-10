# Deployment: local containers and Cloud Run (documented, not deployed)

Status: the **local containers are the verified deliverable**. Cloud Run deployment is **documented
only** in the MVP (ADR 0100 Decision 1): nothing here was deployed and no deploy identity exists.

## Local: docker compose

```bash
export ACME_SIM_TOKENS="customer=$(openssl rand -hex 24),sales=$(openssl rand -hex 24),test-admin=$(openssl rand -hex 24)"
export ACME_SALES_TOKEN=$(echo "$ACME_SIM_TOKENS" | sed -n 's/.*sales=\([^,]*\).*/\1/p')
docker compose -f app/compose.yaml up --build     # then open http://localhost:8080
```

| Service | Image / command | Network | Notes |
|---|---|---|---|
| `frontend` | `app/frontend/Dockerfile` (nginx-unprivileged, uid 101) | web-net, published on `127.0.0.1:8080` | Proxies `/api/*` to the runtime; `/api/a2a` and `/.well-known` are blocked (404). |
| `customer-runtime` | `app/backend/Dockerfile`, `acme-customer` | web-net, a2a-net | Runtime API, Customer agent, A2A skills, in-process simulators. Not published. |
| `sales-agent` | same image, `acme-sales` | sim-net, a2a-net | Holds **only** the `sales` token; `acme-sim` returns 403 for every server except `inventory`. No route to the browser network. |
| `acme-sim` | same image, `acme-sim` | sim-net only | Per-caller tokens (`ACME_SIM_TOKENS`). Not published. |

All containers are non-root (uid 10001, or 101 for nginx), run read-only with `cap_drop: ALL` and
`no-new-privileges`. Images contain no secrets and no `.env` files (`.dockerignore` excludes them);
tokens only come from the environment at run time. `sim-net` and `a2a-net` are `internal` networks.

Demo limitation: the Customer runtime keeps its own in-process simulators (the runtime reads and
writes that world directly), so the standalone `acme-sim` serves the Sales agent's inventory view
from the same seed but a separate state; an accepted upgrade is not reflected in Sales' copy.

`LLM_MODE` defaults to `fake` (offline, $0). The live demo only:

```bash
LLM_MODE=vertex docker compose -f app/compose.yaml up --build
```

Vertex uses ADC only (no API keys, ever), project `acme-airlines-factory`, **`GOOGLE_CLOUD_LOCATION=global`**
(regional locations make the service refuse to start). Provide ADC by mounting your credentials
read-only into `customer-runtime` and setting `GOOGLE_APPLICATION_CREDENTIALS` in a local override file,
never in this repo.

## Hosted demo (IAP)

The operator creates the IAP-protected `acme-irrops-demo` Cloud Run platform with
`infra/90-irrops-demo-platform.sh` (see the [runbook](../setup/01-runbook.md#9-irrops-demo-platform-mocked-scenarios-behind-iap)).

## Single-container demo image (`app/demo/Dockerfile`)

One image for the hosted demo: the built frontend, the runtime API, `acme-sim` and the Sales agent.
`acme-demo` starts the three Python services as loopback-only child processes and serves a front
proxy on `$PORT` (default 8080). `/api/a2a`, `/api/.well-known` and `/mcp/*` return 404 (also when
case-changed or percent-encoded). Per-caller sim tokens are generated at startup, passed to each
child through its own environment only, and never logged. Non-root (uid 10001), `LLM_MODE=fake`.

```bash
docker build -f app/demo/Dockerfile -t acme-demo app && docker run --rm -p 127.0.0.1:8080:8080 acme-demo
```

`.github/workflows/irrops-deploy.yml` builds it on every push to main touching `app/**`, pushes it to
`IRROPS_AR_REPO` and swaps only the image on Cloud Run service `IRROPS_DEMO_SERVICE` (keyless WIF,
`OBSERVATORY_*` variables), then checks the deployed image SHA. If the service does not exist yet it
warns and succeeds. The `containers` CI job builds the image and smokes it (cancellation -> RESOLVED,
`/api/a2a` -> 404).

## Cloud Run (target, not deployed)

One Cloud Run service per container, one service account per service, **no unauthenticated access**:

```bash
PROJECT=acme-airlines-factory; REGION=<a Cloud Run region>   # the Vertex location stays `global`
for SVC in customer-runtime sales-agent acme-sim frontend; do
  gcloud iam service-accounts create "$SVC" --project "$PROJECT"
done
gcloud run deploy acme-sim --image <registry>/acme-backend --command acme-sim --no-allow-unauthenticated \
  --service-account acme-sim@$PROJECT.iam.gserviceaccount.com --set-env-vars ACME_SIM_HOST=0.0.0.0 \
  --set-secrets ACME_SIM_TOKENS=acme-sim-tokens:latest
gcloud run deploy customer-runtime --image <registry>/acme-backend --command acme-customer --no-allow-unauthenticated \
  --service-account customer-runtime@$PROJECT.iam.gserviceaccount.com \
  --set-env-vars ACME_CUSTOMER_HOST=0.0.0.0,LLM_MODE=vertex,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global
gcloud run deploy sales-agent --image <registry>/acme-backend --command acme-sales --no-allow-unauthenticated \
  --service-account sales-agent@$PROJECT.iam.gserviceaccount.com \
  --set-env-vars ACME_SALES_HOST=0.0.0.0,ACME_SIM_URL=<acme-sim url>,ACME_CUSTOMER_URL=<customer-runtime url> \
  --set-secrets ACME_SALES_TOKEN=acme-sales-token:latest
```

Env: `GOOGLE_CLOUD_LOCATION=global` always; **never** set `GOOGLE_API_KEY` or `GEMINI_API_KEY`
(the app refuses to start); model access is through the service account (ADC), granted
`roles/aiplatform.user` on the `customer-runtime` service account only.

### Service-to-service IAM (required)

`roles/run.invoker` is granted per service, to specific service accounts only:

```bash
# Only the sales-agent identity (and the frontend) may invoke the Customer runtime.
gcloud run services add-iam-policy-binding customer-runtime --member serviceAccount:sales-agent@$PROJECT.iam.gserviceaccount.com --role roles/run.invoker
gcloud run services add-iam-policy-binding customer-runtime --member serviceAccount:frontend@$PROJECT.iam.gserviceaccount.com --role roles/run.invoker
# The sales-agent may reach acme-sim (the app token then limits it to `inventory`); the customer too.
gcloud run services add-iam-policy-binding acme-sim --member serviceAccount:sales-agent@$PROJECT.iam.gserviceaccount.com --role roles/run.invoker
gcloud run services add-iam-policy-binding acme-sim --member serviceAccount:customer-runtime@$PROJECT.iam.gserviceaccount.com --role roles/run.invoker
```

`acme-sim` and `customer-runtime` are never `allUsers`/`allAuthenticatedUsers`. This is what backs the
offer's `origin="sales"` claim with identity (the claim itself is only a payload field).

Known gaps to close before a real deployment (follow-ups, not done here):
1. Clients must send the Cloud Run ID token in **`X-Serverless-Authorization`** (the `Authorization` header
   carries the sim bearer token); the HTTP connectors do not do this yet (#100).
2. IAM is per service, not per path: the frontend's invoker role would also let it call `/a2a`. The nginx
   config blocks it, but an app-level check of the caller identity on `/a2a` (the `sales-agent` service
   account only) is still needed (#101).
3. The runtime's in-process simulators must be replaced by `acme-sim` calls for a split deployment (#102).
