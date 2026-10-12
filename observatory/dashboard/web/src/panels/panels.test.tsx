import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

const env = { state: 'ok', as_of: '2026-10-09T00:00:00Z', message: null };
const row = (date: string, vm: number, total: number) => ({
  date,
  vm,
  cloud_run: 1.5,
  vertex_ai: 40,
  other: 0.5,
  total,
});
const spend = {
  ...env,
  days: 30,
  currency: 'USD',
  series: [row('2026-10-08', 10, 52), row('2026-10-09', 14.4, 56.4)],
  totals: { date: 'total', vm: 24.4, cloud_run: 3, vertex_ai: 80, other: 1, total: 108.4 },
};
const models = {
  ...env,
  hours: 24,
  models: [
    { model: 'sonnet', label: 'Sonnet', tokens: 9800000, requests: 1450 },
    { model: 'opus', label: 'Opus', tokens: 1200000, requests: 80 },
  ],
};
const budget = (mtd: number, percent: number) => ({
  ...env,
  budget_usd: 1000,
  mtd_usd: mtd,
  percent,
  markers: [
    { percent: 50, usd: 500, reached: percent >= 50 },
    { percent: 90, usd: 900, reached: percent >= 90 },
    { percent: 100, usd: 1000, reached: percent >= 100 },
  ],
  guard_actions: [
    { ts: '2026-10-10T11:00:09Z', cost_usd: 480, budget_usd: 1000, action: 'no action' },
  ],
});

function mock(over: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (url: string) => {
    const name = url.replace('/api/', '').split('?')[0]!;
    const base: Record<string, unknown> = {
      spend,
      models,
      budget: budget(528.98, 52.9),
      agents: { ...env, agents: [] },
      github: { ...env, snapshot: null, merges_per_day: [] },
      'agent-engine': { ...env, hours: 1, engines: [] },
      'collector-health': { ...env, healthy: true, snapshot_age_seconds: 30, last_execution: null },
    };
    return { ok: true, status: 200, json: async () => over[name] ?? base[name] };
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
afterEach(() => vi.unstubAllGlobals());

describe('Spend', () => {
  it('shows totals, legend and an accessible daily table', async () => {
    mock();
    render(<App />);
    const p = screen.getByRole('region', { name: 'Spend' });
    expect(await within(p).findByText('$108.40')).toBeInTheDocument();
    expect(within(p).getByText(/Vertex AI \$80\.00/)).toBeInTheDocument();
    const table = within(p).getByRole('table', { name: /Daily spend/ });
    expect(within(table).getByRole('row', { name: /2026-10-09.*\$56\.40/ })).toBeInTheDocument();
  });
  it('says billing export is not enabled when empty, never $0', async () => {
    mock({ spend: { ...env, state: 'empty', as_of: null, message: 'x', series: [] } });
    render(<App />);
    const p = screen.getByRole('region', { name: 'Spend' });
    expect(await within(p).findByRole('status')).toHaveTextContent(
      'Billing export not enabled yet',
    );
    expect(within(p).queryByText(/\$0/)).not.toBeInTheDocument();
  });
});

describe('Models', () => {
  it('shows tokens and requests and refetches for 7d', async () => {
    const f = mock();
    render(<App />);
    const p = screen.getByRole('region', { name: 'Model usage' });
    expect(await within(p).findByText('9,800,000')).toBeInTheDocument();
    expect(within(p).getByText('1,450')).toBeInTheDocument();
    await userEvent.click(within(p).getByRole('button', { name: '7d' }));
    expect(f).toHaveBeenCalledWith('/api/models?hours=168', expect.anything());
  });
});

describe('Budget', () => {
  it('shows MTD, markers and guard actions', async () => {
    mock();
    render(<App />);
    const p = screen.getByRole('region', { name: 'Budget' });
    expect(await within(p).findByText('$528.98')).toBeInTheDocument();
    expect(
      within(p).getByText('Notice: half of budget used', { exact: false }),
    ).toBeInTheDocument();
    expect(within(p).getByText(/90% \(\$900\.00\): not reached/)).toBeInTheDocument();
    expect(within(p).getByText(/\$480\.00 of \$1,000\.00 → no action/)).toBeInTheDocument();
  });
  it('warns in words at >= 90% (not colour alone)', async () => {
    mock({ budget: budget(920, 92) });
    render(<App />);
    const p = screen.getByRole('region', { name: 'Budget' });
    expect(await within(p).findByText(/Warning: 90% of budget reached/)).toBeInTheDocument();
    expect(within(p).getByRole('meter')).toHaveAttribute('aria-valuenow', '92');
  });
  it('says billing export not enabled when empty', async () => {
    mock({ budget: { ...budget(0, 0), state: 'empty', as_of: null } });
    render(<App />);
    const p = screen.getByRole('region', { name: 'Budget' });
    expect(await within(p).findByRole('status')).toHaveTextContent(
      'Billing export not enabled yet',
    );
  });
});
