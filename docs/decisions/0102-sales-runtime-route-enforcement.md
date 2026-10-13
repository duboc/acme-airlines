# 0102. Runtime enforcement: Sales reaches Customer through `/a2a` only

Status: accepted (issue #230)

## Context
"Sales never contacts passengers" was enforced by static source scans only. At runtime Sales
shared a network and one unauthenticated app with every Customer route (WhatsApp send,
scenario reset, journeys). `docs/agent-engine/engines.md` accepted this as a residual risk.

## Decision
- **Caller identity.** Sales presents its per-caller credential (`ACME_SALES_TOKEN`, the same
  value acme-sim knows as `sales`) as a bearer token. The Customer runtime is given the token
  and recognises it (`acme.api.a2a_guard.SalesRouteGuard`).
- **Fail closed.** A Sales-identified request is allowed for exactly `POST /a2a`. Anything else
  (any path, method or websocket) gets 403 and an `acme.audit` log event plus a
  `sales_route_denied` guardrail metric. The credential is never logged.
- **Separate listener.** `acme-customer` also serves an A2A-only app (`create_a2a_app`: agent
  card and `/a2a`, no passenger route exists) on `ACME_A2A_PORT` (8802). Compose and the
  single-container demo point Sales at it.

## Consequences
Residual risk: in `ENGINE_MODE=agent-engine` the guard does not apply (IAM is shared, see
`docs/agent-engine/engines.md`), and a process holding no Sales token is not fenced; the
network listener split and credential check cover the local and compose modes. The
`a2a-net` still reaches port 8800 at the network layer, which is why the credential check
stays on the main app too.
