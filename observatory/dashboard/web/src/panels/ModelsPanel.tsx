import { useState } from 'react';
import { Panel } from '../Panel';
import { num } from '../format';
import { useApi } from '../useApi';

const RANGES = [
  ['24h', 24],
  ['7d', 168],
] as const;

export function ModelsPanel() {
  const [hours, setHours] = useState<number>(24);
  const result = useApi('models', `?hours=${hours}`);
  return (
    <Panel
      id="models"
      title="Model usage"
      result={result}
      emptyMessage="No model usage recorded yet."
    >
      {(data) => {
        const max = Math.max(...data.models.map((m) => m.tokens), 1);
        return (
          <>
            <div role="group" aria-label="Time range" className="seg">
              {RANGES.map(([label, h]) => (
                <button
                  key={h}
                  type="button"
                  aria-pressed={hours === h}
                  onClick={() => setHours(h)}
                >
                  {label}
                </button>
              ))}
            </div>
            <table>
              <caption className="sr-only">
                Tokens and requests per model, last {data.hours} hours
              </caption>
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col">Tokens</th>
                  <th scope="col">Requests</th>
                </tr>
              </thead>
              <tbody>
                {data.models.map((m) => (
                  <tr key={m.model}>
                    <th scope="row">{m.label}</th>
                    <td>
                      {num(m.tokens)}
                      <span
                        className="bar"
                        aria-hidden="true"
                        style={{ width: `${(m.tokens / max) * 100}%` }}
                      />
                    </td>
                    <td>{num(m.requests)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        );
      }}
    </Panel>
  );
}
