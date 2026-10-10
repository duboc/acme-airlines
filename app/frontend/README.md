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

## Operator console (`src/console/`)

Trigger/Reset controls, a journey card (status stepper + itinerary) and a live timeline fed by
SSE (`/api/journeys/{id}/events`); the card refetches `/api/journeys/{id}` on each event.
Guardrail decisions (`actor: guardrail`, `payload.approved`, `payload.reasons[]`) and
`sales_suppressed` events render in distinct styles with their reasons. Reason codes are mapped
to plain text in `labels.ts`; unknown codes are shown raw. Tests mock `fetch` and `EventSource`.
