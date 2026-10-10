import { type SpendResponse } from '../api';
import { usd } from '../format';

const SERIES = [
  ['vm', 'VM (Compute Engine)', 'var(--s1)'],
  ['cloud_run', 'Cloud Run', 'var(--s2)'],
  ['vertex_ai', 'Vertex AI', 'var(--s3)'],
  ['other', 'Other', 'var(--s4)'],
] as const;

export function SpendBody({ data }: { data: SpendResponse }) {
  const W = 360;
  const H = 120;
  const max = Math.max(...data.series.map((r) => r.total), 0.01);
  const bw = W / Math.max(data.series.length, 1);
  return (
    <>
      <p className="big">
        {usd(data.totals.total)} <span className="unit">over {data.days} days</span>
      </p>
      <ul className="legend">
        {SERIES.map(([k, label, color]) => (
          <li key={k}>
            <span className="swatch" style={{ background: color }} aria-hidden="true" />
            {label} {usd(data.totals[k])}
          </li>
        ))}
      </ul>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" aria-hidden="true" focusable="false">
        {data.series.map((r, i) => {
          let y = H;
          return (
            <g key={r.date}>
              {SERIES.map(([k, , color]) => {
                const h = (r[k] / max) * H;
                y -= h;
                return (
                  <rect
                    key={k}
                    x={i * bw + 1}
                    y={y}
                    width={Math.max(bw - 2, 1)}
                    height={h}
                    fill={color}
                  />
                );
              })}
            </g>
          );
        })}
      </svg>
      <details>
        <summary>Daily table</summary>
        <table>
          <caption className="sr-only">Daily spend by service, USD</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              {SERIES.map(([k, label]) => (
                <th key={k} scope="col">
                  {label}
                </th>
              ))}
              <th scope="col">Total</th>
            </tr>
          </thead>
          <tbody>
            {[...data.series].reverse().map((r) => (
              <tr key={r.date}>
                <th scope="row">{r.date}</th>
                {SERIES.map(([k]) => (
                  <td key={k}>{usd(r[k])}</td>
                ))}
                <td>{usd(r.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </>
  );
}
