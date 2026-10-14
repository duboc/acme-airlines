import { useState } from 'react';
import type { IrropsResponse } from '../api';
import { Panel } from '../Panel';
import { num } from '../format';
import { useApi } from '../useApi';

const RANGES = [
  ['24h', 24],
  ['7d', 168],
] as const;
const HEALTH = {
  healthy: ['●', 'Healthy'],
  degraded: ['▲', 'Degraded'],
  down: ['■', 'Down'],
  unknown: ['○', 'Unknown'],
} as const;
const PENDING = 'No data yet';

function Stat({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd className={value === null ? 'pending' : undefined}>{value ?? PENDING}</dd>
    </div>
  );
}

// `?? null`: a field the API omits is pending, never a crash or a zero.
const count = (n?: number | null) => (n == null ? null : num(n));
const secs = (n?: number | null) => (n == null ? null : `${n.toFixed(1)} s`);

function IrropsBody({ data: d }: { data: IrropsResponse }) {
  const [glyph, health] = HEALTH[d.demo_health];
  const errors =
    d.errors_5xx == null
      ? null
      : d.errors_5xx === 0
        ? '0'
        : `${num(d.errors_5xx)} (${d.errors_5xx_per_hour} per hour)`;
  return (
    <>
      <dl className="stats">
        <div className="stat">
          <dt>Hosted demo</dt>
          <dd>
            <span aria-hidden="true">{glyph} </span>
            {health}
          </dd>
        </div>
        <Stat label="HTTP 5xx" value={errors} />
        <Stat label="Notification p50" value={secs(d.notification_p50_s)} />
        <Stat label="Notification p95" value={secs(d.notification_p95_s)} />
        <Stat label="Offers proposed" value={count(d.offers_proposed)} />
        <Stat label="Accepted" value={count(d.offers_accepted)} />
        <Stat label="Declined" value={count(d.offers_declined)} />
        <Stat
          label="Template fallback rate"
          value={
            d.template_fallback_rate === null
              ? null
              : `${Math.round(d.template_fallback_rate * 100)}%`
          }
        />
        <Stat label="Needs human" value={count(d.needs_human)} />
      </dl>
      {d.guardrail.length === 0 ? (
        <p className="pending">No guardrail decisions yet.</p>
      ) : (
        <table>
          <caption>Guardrail decisions by rule and outcome, last {d.hours} hours</caption>
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col">Outcome</th>
              <th scope="col">Decisions</th>
            </tr>
          </thead>
          <tbody>
            {d.guardrail.map((g) => (
              <tr key={`${g.rule}-${g.outcome}`}>
                <th scope="row">{g.rule}</th>
                <td>{g.outcome === 'approve' ? 'Approved' : 'Rejected'}</td>
                <td>{num(g.count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

export function IrropsPanel() {
  const [hours, setHours] = useState<number>(24);
  const result = useApi('irrops', `?hours=${hours}`);
  return (
    <Panel
      id="irrops"
      title="IRROPS product"
      result={result}
      emptyMessage="No IRROPS metrics yet: the runtime has not reported."
    >
      {(data) => (
        <>
          <div role="group" aria-label="Time range" className="seg">
            {RANGES.map(([label, h]) => (
              <button key={h} type="button" aria-pressed={hours === h} onClick={() => setHours(h)}>
                {label}
              </button>
            ))}
          </div>
          <IrropsBody data={data} />
        </>
      )}
    </Panel>
  );
}
