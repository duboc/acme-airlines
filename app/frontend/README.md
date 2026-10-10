# ACME frontend

Vite + React + TypeScript (strict). Two views: the operations console (`/console`) and the
simulated WhatsApp passenger chat (`/whatsapp`). See ADR 0100 (`docs/decisions/0100-app-stack.md`).
Design context: `PRODUCT.md` and `DESIGN.md` at the repo root.

| Command         | What it does                                   |
| --------------- | ---------------------------------------------- |
| `npm ci`        | Install pinned dependencies (Node 22+)         |
| `npm run dev`   | Dev server; `/api` proxies to `localhost:8000` |
| `npm run lint`  | ESLint (incl. a11y rules) + Prettier check     |
| `npm test`      | Vitest + Testing Library                       |
| `npm run build` | Type-check and production build                |

`src/api/types.ts` is a hand-written stub until the OpenAPI contract (#17) merges; then it
should be generated from `app/backend/openapi.json`. The UI talks only to the app's own API.
