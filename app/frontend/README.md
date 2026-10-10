# ACME frontend

Vite + React + TypeScript (strict). Two views: the operations console (`/console`) and the
simulated WhatsApp passenger chat (`/whatsapp`). See ADR 0100 (`docs/decisions/0100-app-stack.md`).
Design context: `PRODUCT.md` and `DESIGN.md` at the repo root.

| Command         | What it does                                                                   |
| --------------- | ------------------------------------------------------------------------------ |
| `npm ci`        | Install pinned dependencies (Node 22+)                                         |
| `npm run dev`   | Dev server; `/api` proxies to `localhost:8000` with the `/api` prefix stripped |
| `npm run lint`  | ESLint (incl. a11y rules) + Prettier check                                     |
| `npm test`      | Vitest + Testing Library                                                       |
| `npm run build` | Type-check and production build                                                |

`src/api/types.ts` is a hand-written stub until the OpenAPI contract (#17) merges; then it
should be generated from `app/backend/openapi.json`. The UI talks only to the app's own API.

**Dev proxy:** the UI calls `/api/*`, and the backend serves its routes at the root, so `devProxy.ts` rewrites `^/api` to an empty prefix (`/api/journeys` reaches `/journeys`). Any other host (e.g. the production container) must do the same rewrite.

## End-to-end test (`e2e/`, Playwright)

`npm run e2e` drives Mariana's full story through the console and the WhatsApp view: cancellation
→ RESOLVED (AC1240, 14A, HT8842) → one WhatsApp message → "after 10am" → AC1252 11:40 → no offer
bubble until "Perfect, thanks!" → optional $135 offer → Accept → seat 2A, journey CLOSED,
verification passed. It starts `acme-sim`, the Customer API (`e2e/serve_customer.py`: FakeLlm and a
fixed clock that the test advances through `POST /e2e/advance`), `acme-sales` and the Vite dev
server itself, and runs headless in about 30 s.

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 22+, ports 5173, 8000, 8700 and 8801 free, and
once: `npm ci && npx playwright install chromium` (add `--with-deps` on a fresh Linux box).
Locally, already running servers are reused. CI runs the `e2e` workflow
(`.github/workflows/e2e.yml`, on PRs touching `app/`) and uploads `playwright-report` and the
trace as the `playwright-trace` artifact on failure; view with `npx playwright show-trace <trace.zip>`.

## Operator console (`src/console/`)

Trigger/Reset controls, a journey card (status stepper + itinerary) and a live timeline fed by
SSE (`/api/journeys/{id}/events`); the card refetches `/api/journeys/{id}` on each event.
Guardrail decisions (`actor: guardrail`, `payload.approved`, `payload.reasons[]`) and
`sales_suppressed` events render in distinct styles with their reasons. Reason codes are mapped
to plain text in `labels.ts`; unknown codes are shown raw. Tests mock `fetch` and `EventSource`.

On load, the console fetches `GET /journeys`, attaches to the first open journey and resubscribes
to its SSE stream (which replays the timeline), so a refresh keeps context. Trigger is disabled
while a journey is open; use Reset to start again.
