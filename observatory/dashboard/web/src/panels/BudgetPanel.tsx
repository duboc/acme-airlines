import type { BudgetResponse } from '../api';
import { usd } from '../format';

export function thresholdLabel(percent: number): { glyph: string; text: string } {
  if (percent >= 100) return { glyph: '■', text: 'Over budget' };
  if (percent >= 90) return { glyph: '▲', text: 'Warning: 90% of budget reached' };
  if (percent >= 50) return { glyph: '●', text: 'Notice: half of budget used' };
  return { glyph: '●', text: 'Within budget' };
}

export function BudgetBody({ data }: { data: BudgetResponse }) {
  const t = thresholdLabel(data.percent);
  const warn = data.percent >= 90;
  return (
    <>
      <p className="big">
        {usd(data.mtd_usd)} <span className="unit">of {usd(data.budget_usd)} this month</span>
      </p>
      <p className={warn ? 'threshold threshold-warn' : 'threshold'}>
        <span aria-hidden="true">{t.glyph} </span>
        {t.text} ({data.percent.toFixed(1)}%)
      </p>
      <div
        className="meter"
        role="meter"
        aria-label="Month-to-date spend"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(data.percent, 100)}
        aria-valuetext={`${data.percent.toFixed(1)}% of budget`}
      >
        <span className="meter-fill" style={{ width: `${Math.min(data.percent, 100)}%` }} />
        {data.markers.map((m) => (
          <span
            key={m.percent}
            className="marker"
            style={{ left: `${Math.min(m.percent, 100)}%` }}
          />
        ))}
      </div>
      <ul className="markers">
        {data.markers.map((m) => (
          <li key={m.percent}>
            {m.percent}% ({usd(m.usd)}): {m.reached ? 'reached' : 'not reached'}
          </li>
        ))}
      </ul>
      <h3>Budget guard actions</h3>
      {data.guard_actions.length === 0 ? (
        <p>No guard runs recorded yet.</p>
      ) : (
        <ul className="guard">
          {data.guard_actions.slice(0, 5).map((g) => (
            <li key={g.ts}>
              <time dateTime={g.ts}>{g.ts.replace('T', ' ').replace(/:\d\dZ$/, 'Z')}</time>:{' '}
              {usd(g.cost_usd)} of {usd(g.budget_usd)} → {g.action}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
