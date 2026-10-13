# IRROPS product metrics and proposed alert policies (#235)

The runtime emits OpenTelemetry metrics (`app/backend/src/acme/metrics.py`). Attribute values are
ids/enums only, never passenger data. In deploy, `configure_cloud_monitoring` (called next to
`configure_cloud_trace`, same `ACME_ENABLE_TRACING` switch) exports them to Cloud Monitoring as
`workload.googleapis.com/<name>`. Tests use an in-memory reader.

| Metric | Type | Attributes | Meaning |
|--------|------|------------|---------|
| `acme.guardrail.decisions` | counter | `rule` (reason code, or `all` on approve), `outcome` (approve/reject), `step` | Guardrail decisions; one point per failed rule |
| `acme.offers` | counter | `status` (proposed/accepted/declined) | Offer lifecycle |
| `acme.llm.template_fallbacks` | counter | `surface` (resolution/offer) | LLM text rejected, template used |
| `acme.needs_human` | counter | `specialist` | Escalations to a person |
| `acme.notification.latency` | histogram (s) | none | RESOLVED to `notification_sent` |
| `acme.http.server.errors` | counter | `status_code` | HTTP 5xx responses from the API |

## Proposed alert policies (operator creates them; needs infra access)

1. **5xx rate**: `acme.http.server.errors` rate > 0.1/s for 5 min, or any 5xx for 10 min on the
   hosted demo. Severity: page.
2. **needs_human spike**: `acme.needs_human` increase > 3 in 15 min. Severity: ticket.
3. **Collector down**: absence of `acme.notification.latency` or any `acme.*` series for 15 min
   while the demo is up (MQL/PromQL absence condition). Severity: ticket.
4. Optional: template fallback ratio (`acme.llm.template_fallbacks` / notifications) > 50 % in
   `vertex` mode signals a model or prompt regression.
