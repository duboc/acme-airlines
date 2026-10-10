import type { GithubResponse } from '../api';

export function GithubBody({ data }: { data: GithubResponse }) {
  const s = data.snapshot;
  if (!s) return <p>No snapshot received yet.</p>;
  const max = Math.max(...data.merges_per_day.map((d) => d.count), 1);
  const rate = s.ci_pass_rate === null ? 'unknown' : `${Math.round(s.ci_pass_rate * 100)}%`;
  return (
    <>
      <p className="big">
        {rate} <span className="unit">CI pass rate · {s.merges_24h} merges in 24h</span>
      </p>
      <h3>Issues by status</h3>
      <ul className="legend">
        {s.issues_by_status.map((c) => (
          <li key={c.key}>
            {c.key}: <strong>{c.count}</strong>
          </li>
        ))}
      </ul>
      <h3>Open PRs</h3>
      {s.open_prs.length === 0 ? (
        <p>No open PRs.</p>
      ) : (
        <table>
          <caption className="sr-only">
            Open pull requests with latest review verdict and CI
          </caption>
          <thead>
            <tr>
              <th scope="col">PR</th>
              <th scope="col">Verdict</th>
              <th scope="col">CI</th>
            </tr>
          </thead>
          <tbody>
            {s.open_prs.map((p) => (
              <tr key={p.number}>
                <th scope="row">
                  #{p.number} {p.title}
                </th>
                <td>{p.latest_verdict ?? 'No verdict'}</td>
                <td>{p.ci_conclusion ?? 'unknown'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3>Merges per day</h3>
      <svg viewBox="0 0 280 40" className="chart" aria-hidden="true" focusable="false">
        {data.merges_per_day.map((d, i) => (
          <rect
            key={d.date}
            x={i * 20 + 2}
            y={40 - (d.count / max) * 40}
            width={16}
            height={(d.count / max) * 40}
            fill="var(--s1)"
          />
        ))}
      </svg>
      <details>
        <summary>Merges per day, table</summary>
        <table>
          <caption className="sr-only">Merges per day, last 14 days</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Merges</th>
            </tr>
          </thead>
          <tbody>
            {data.merges_per_day.map((d) => (
              <tr key={d.date}>
                <th scope="row">{d.date}</th>
                <td>{d.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </>
  );
}
