import { Panel } from '../Panel';
import type { AgentEngine } from '../api';
import { num } from '../format';
import { useApi } from '../useApi';

const ms = (v: number | null) =>
  v === null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`;
const pct = (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`);

function Engine({ e }: { e: AgentEngine }) {
  const titleId = `ae-${e.engine_id}`;
  return (
    <article className="engine" aria-labelledby={titleId}>
      <h3 id={titleId}>{e.name}</h3>
      <dl className="metrics">
        <div>
          <dt>p50</dt>
          <dd>{ms(e.latency_p50_ms)}</dd>
        </div>
        <div>
          <dt>p95</dt>
          <dd>{ms(e.latency_p95_ms)}</dd>
        </div>
        <div>
          <dt>Error rate</dt>
          <dd>{pct(e.error_rate)}</dd>
        </div>
        <div>
          <dt>Requests</dt>
          <dd>{num(e.requests)}</dd>
        </div>
      </dl>
      {e.trace_access === 'not_granted' || e.traces === null ? (
        <p className="note">Trace access not granted</p>
      ) : e.traces.length === 0 ? (
        <p className="note">No recent traces.</p>
      ) : (
        <ul className="traces" aria-label={`Recent traces for ${e.name}`}>
          {e.traces.map((t) => (
            <li key={t.trace_id}>
              <a href={t.console_url} target="_blank" rel="noopener noreferrer">
                {t.name} <code>{t.trace_id.slice(0, 8)}</code>
              </a>{' '}
              <span>
                {ms(t.duration_ms)}
                {t.status === 'error' && <strong> ■ error</strong>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

export function AgentEnginePanel() {
  const result = useApi('agent-engine', '?hours=1');
  return (
    <Panel
      id="agent-engine"
      title="Agent Engine"
      result={result}
      emptyMessage="No Agent Engines configured yet."
    >
      {(data) => (
        <>
          <p className="note">Last {data.hours} h</p>
          {data.engines.map((e) => (
            <Engine key={e.engine_id} e={e} />
          ))}
        </>
      )}
    </Panel>
  );
}
