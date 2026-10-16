# ACME frontend

Vite + React + TypeScript (strict). Two views: the operations console (`/console`) and the
simulated WhatsApp passenger chat (`/whatsapp`). See `docs/product/` for product and architecture specs.
Design context: `PRODUCT.md` and `DESIGN.md` at the repo root.

| Command           | What it does                                                                                                                                                                                                                             |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `npm ci`          | Install pinned dependencies (Node 22+)                                                                                                                                                                                                   |
| `npm run dev`     | Dev server; `devProxy.ts` proxies `/api` to `localhost:8000` with `/api` stripped (`E2E_CUSTOMER_PORT=8000` in `e2e/serve_customer.py`; set `ACME_CUSTOMER_PORT=8000` when running standalone `acme-customer`, which defaults to `8800`) |
| `npm run build`   | Type-check (`tsc -b`) and production build (`vite build`)                                                                                                                                                                                |
| `npm run lint`    | ESLint (incl. a11y rules) + Prettier check                                                                                                                                                                                               |
| `npm run format`  | Prettier write across the frontend workspace                                                                                                                                                                                             |
| `npm test`        | Vitest + Testing Library                                                                                                                                                                                                                 |
| `npm run preview` | Preview the production build locally (`vite preview`)                                                                                                                                                                                    |
| `npm run e2e`     | Playwright E2E suite (`e2e/narrative.spec.ts`, `e2e/scenarios.spec.ts`)                                                                                                                                                                  |

`src/api/types.ts` defines the disruption state enum (`DISRUPTION_STATES`), while
`src/console/contract.test.ts` drift-checks `src/console/types.ts`, `src/console/fixtures.ts`, and
`src/console/labels.ts` directly against the committed `app/backend/openapi.json`. The UI talks only
to the app's own API.

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

On load, the console fetches `GET /journeys`, attaches to the first non-`CLOSED` journey (or falls
back to `all[0]` so a `CLOSED` journey stays visible across refreshes), and resubscribes to its SSE
stream (which replays the timeline). `Trigger AC1234 cancellation` is disabled whenever any journey
is loaded (`journey !== null`, including `CLOSED`) until `Reset` is clicked.

## Guided scenario runner (`src/demo/`)

A "Guided scenario" panel on `/console`, labelled "Demo, all systems mocked", with the WhatsApp
chat beside the journey card and timeline. Pick a preset (from `GET /scenarios`), **Start
scenario**, then **Next step** runs the next scripted action. After each step, "Expected now"
says what the tester should see and compares it with the observed state (`expectations.ts`),
showing "Matches" or "Differs" plus the differing fields. Manual passenger actions (quick replies,
Accept, No thanks) use the WhatsApp API. In `ENGINE_MODE=local`, `/scenarios*` requires
`LLM_MODE=fake` (`404` in `vertex` mode, so the panel reports that scenarios are unavailable); in
`ENGINE_MODE=agent-engine`, presets remain enabled even when the Customer Agent Engine runs with
`LLM_MODE=vertex`. E2E: `e2e/scenarios.spec.ts` (5 tests: `happy-path`, `seat-taken`,
`declines-upgrade`, `upgrade-race`, and the 10-minute `no-offer` service closure).
