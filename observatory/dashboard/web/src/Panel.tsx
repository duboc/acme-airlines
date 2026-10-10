import type { ReactNode } from 'react';
import type { Envelope } from './api';
import type { Result } from './useApi';

const CHIPS = {
  loading: ['◌', 'Loading…'],
  error: ['■', 'Failed to load'],
  ok: ['●', 'Live'],
  empty: ['○', 'No data yet'],
  lagging: ['▲', 'Delayed'],
  unavailable: ['■', 'Unavailable'],
} as const;

export function formatAsOf(iso: string | null): string {
  return iso ? iso.replace('T', ' ').replace(/:\d\dZ$/, 'Z') : '—';
}

interface Props<T extends Envelope> {
  id: string;
  title: string;
  result: Result<T>;
  /** Panel body for ok and lagging data; slices #119/#120 supply it. */
  children?: (data: T) => ReactNode;
}

/** Shared frame: title, state chip, as_of, explicit message; body only when data exists. */
export function Panel<T extends Envelope>({ id, title, result, children }: Props<T>) {
  const key = result.status === 'done' ? result.data.state : result.status;
  const [glyph, label] = CHIPS[key];
  const data = result.status === 'done' ? result.data : null;
  const message =
    result.status === 'error'
      ? `Could not reach the API (${result.error}). Other panels are unaffected.`
      : data?.state === 'empty'
        ? (data.message ?? 'The source has no rows yet.')
        : (data?.message ?? null);
  const showBody = data && (data.state === 'ok' || data.state === 'lagging');
  return (
    <section className={`panel panel-${key}`} aria-labelledby={`${id}-title`}>
      <header className="panel-head">
        <h2 id={`${id}-title`}>{title}</h2>
        <span className="chip">
          <span aria-hidden="true">{glyph} </span>
          {label}
        </span>
      </header>
      {data?.as_of && <p className="asof">As of {formatAsOf(data.as_of)}</p>}
      {message && (
        <p className="message" role="status">
          {message}
        </p>
      )}
      {showBody && <div className="panel-body">{children ? children(data) : null}</div>}
    </section>
  );
}
