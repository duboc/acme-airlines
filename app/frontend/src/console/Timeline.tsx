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
                  {reasons.map((r) => (
                    <span key={r}>{reasonLabel(r)}</span>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
