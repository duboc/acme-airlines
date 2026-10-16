import { eventLabel, reasonLabel } from './labels';
import type { JourneyEvent } from './types';

function kindOf(e: JourneyEvent): string {
  if (e.type === 'sales_suppressed') return 'sales-suppressed';
  if (e.actor === 'guardrail')
    return e.payload?.approved === true ? 'guardrail-approved' : 'guardrail-rejected';
  return 'plain';
}

function reasonsOf(e: JourneyEvent): string[] {
  const p = e.payload;
  if (!p) return [];
  if (Array.isArray(p.reasons)) return p.reasons.map(String);
  const out: string[] = [];
  if (typeof p.reason === 'string' && p.reason) {
    out.push(p.reason);
  }
  if (typeof p.error === 'string' && p.error) {
    const step = typeof p.step === 'string' && p.step ? `${p.step}: ` : '';
    out.push(`${step}${p.error}`);
  }
  if (p.rule_reasons && typeof p.rule_reasons === 'object' && !Array.isArray(p.rule_reasons)) {
    for (const [k, v] of Object.entries(p.rule_reasons as Record<string, unknown>)) {
      if (typeof v === 'string' && v) out.push(`${k}: ${v}`);
    }
  }
  if (e.type === 'connection_constraint' && typeof p.text === 'string' && p.text) {
    out.push(p.text);
  }
  if (
    e.type === 'connection_verified' &&
    typeof p.flight === 'string' &&
    typeof p.connection_flight === 'string' &&
    typeof p.margin_minutes === 'number'
  ) {
    out.push(`${p.flight} → ${p.connection_flight} (margin ${p.margin_minutes} min)`);
  }
  if (
    e.type === 'status_changed' &&
    typeof p.from === 'string' &&
    p.from &&
    typeof p.to === 'string' &&
    p.to
  ) {
    out.push(`${p.from} → ${p.to}`);
  }
  if (e.type === 'rebooked' && typeof p.flight === 'string' && p.flight && !p.reason) {
    out.push(p.flight);
  }
  if (e.type === 'bag_rerouted' && typeof p.tag === 'string' && p.tag) {
    out.push(typeof p.flight === 'string' && p.flight ? `${p.tag} → ${p.flight}` : p.tag);
  }
  if (
    e.type === 'upgrade_confirmed' &&
    typeof p.flight === 'string' &&
    p.flight &&
    typeof p.seat === 'string' &&
    p.seat &&
    typeof p.price === 'string' &&
    p.price
  ) {
    const price = p.price.startsWith('$') ? p.price : `$${p.price}`;
    out.push(`${p.flight} · ${p.seat} (${price})`);
  }
  if (
    e.type === 'seat_released' &&
    typeof p.seat === 'string' &&
    p.seat &&
    typeof p.replaced_by === 'string' &&
    p.replaced_by
  ) {
    out.push(`${p.seat} → ${p.replaced_by}`);
  }
  if (e.type === 'change_handoff' && typeof p.after === 'string' && p.after) {
    out.push(`after ${p.after}`);
  }
  if (e.type === 'request_closed' && typeof p.outcome === 'string' && p.outcome) {
    out.push(p.outcome);
  }
  if (e.type === 'resolution_blocked' && Array.isArray(p.missing) && p.missing.length > 0) {
    out.push(`missing: ${p.missing.map(String).join(', ')}`);
  }
  if (e.type === 'verification_failed') {
    const details =
      p.details && typeof p.details === 'object' && !Array.isArray(p.details)
        ? Object.entries(p.details as Record<string, unknown>).filter(
            ([, v]) => typeof v === 'string' && v,
          )
        : [];
    if (details.length > 0) {
      for (const [k, v] of details) {
        out.push(`${k}: ${v as string}`);
      }
    } else if (Array.isArray(p.failed) && p.failed.length > 0) {
      for (const f of p.failed) {
        if (typeof f === 'string' && f) out.push(f);
      }
    }
  }
  return out;
}

export function Timeline({ events }: { events: JourneyEvent[] }) {
  return (
    <section className="panel" aria-labelledby="timeline-h">
      <h2 id="timeline-h">Timeline</h2>
      {events.length === 0 && <p>Waiting for events.</p>}
      <ol role="log" aria-label="Agent timeline" aria-live="polite" className="timeline">
        {events.map((e, i) => {
          const kind = kindOf(e);
          const reasons = reasonsOf(e);
          return (
            <li key={i} data-kind={kind}>
              <time dateTime={e.timestamp}>
                {new Date(e.timestamp).toISOString().slice(11, 19)}
              </time>
              <span className="actor">{e.actor}</span>
              <span className="event">
                {kind === 'sales-suppressed' ? 'Sales held back' : eventLabel(e.type)}
                {kind === 'guardrail-rejected' && <strong> Rejected</strong>}
                {kind === 'guardrail-approved' && <strong> Approved</strong>}
              </span>
              {reasons.length > 0 && (
                <div className="reasons">
                  {reasons.flatMap((r, idx) =>
                    idx > 0
                      ? [' ', <span key={r}>{reasonLabel(r)}</span>]
                      : [<span key={r}>{reasonLabel(r)}</span>],
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
