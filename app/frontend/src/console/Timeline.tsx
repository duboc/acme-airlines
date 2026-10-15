import { eventLabel, reasonLabel } from './labels';
import type { JourneyEvent } from './types';

function kindOf(e: JourneyEvent): string {
  if (e.type === 'sales_suppressed') return 'sales-suppressed';
  if (e.actor === 'guardrail')
    return e.payload?.approved === true ? 'guardrail-approved' : 'guardrail-rejected';
  return 'plain';
}

function reasonsOf(e: JourneyEvent): string[] {
  const r = e.payload?.reasons;
  if (Array.isArray(r)) return r.map(String);
  if (e.type === 'sales_suppressed' && e.payload?.reason) return [String(e.payload.reason)];
  return [];
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
