import { render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const zero = { date: 'total', vm: 0, cloud_run: 0, vertex_ai: 0, other: 0, total: 0 };
const ok = {
  state: 'ok',
  as_of: '2026-10-10T12:00:00Z',
  message: null,
  days: 30,
  hours: 24,
  series: [],
  totals: zero,
  models: [],
  budget_usd: 1000,
  mtd_usd: 0,
  percent: 0,
  markers: [],
  guard_actions: [],
  agents: [],
  lead_last_activity: null,
  limits_exceeded_count: 0,
  snapshot: null,
  merges_per_day: [],
  engines: [],
};

function mockApi(overrides: Record<string, unknown> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const name = url.replace('/api/', '').split('?')[0]!;
      const body = overrides[name] ?? ok;
      if (body instanceof Error) throw body;
      return { ok: true, status: 200, json: async () => body };
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('App shell', () => {
  it('has landmarks, one h1 and six titled panels', async () => {
    mockApi();
    render(<App />);
    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'ACME Observatory' })).toBeInTheDocument();
    for (const t of ['Spend', 'Model usage', 'Agents', 'Agent Engine', 'GitHub', 'Budget']) {
      expect(screen.getByRole('region', { name: t })).toBeInTheDocument();
    }
    expect(await screen.findAllByText('Live')).toHaveLength(6);
    expect(screen.getByText('Latest data 2026-10-10 12:00Z')).toBeInTheDocument();
  });

  it('hides the fake-data badge unless VITE_DATA_MODE=fake (production image)', () => {
    mockApi();
    const { unmount } = render(<App />);
    expect(screen.queryByText('Fake data')).not.toBeInTheDocument();
    unmount();
  });

  it('first focusable element is the skip link', () => {
    mockApi();
    render(<App />);
    expect(screen.getAllByRole('link')[0]).toHaveAttribute('href', '#main');
  });

  it.each([
    ['empty', 'No data yet', 'No agent snapshots yet.'],
    ['lagging', 'Delayed', 'Billing data is 3 days old.'],
    ['unavailable', 'Unavailable', 'Billing export is not enabled.'],
  ])('shows an explicit %s state', async (state, chip, message) => {
    mockApi({ agents: { ...ok, state, as_of: null, message } });
    render(<App />);
    const panel = screen.getByRole('region', { name: 'Agents' });
    expect(await within(panel).findByText(chip)).toBeInTheDocument();
    expect(within(panel).getByRole('status')).toHaveTextContent(message);
  });

  it('shows a failure in one panel without blanking others', async () => {
    mockApi({ models: new Error('boom') });
    render(<App />);
    const panel = screen.getByRole('region', { name: 'Model usage' });
    expect(await within(panel).findByText('Failed to load')).toBeInTheDocument();
    expect(within(panel).getByRole('status')).toHaveTextContent('Could not reach the API');
    expect(await screen.findAllByText('Live')).toHaveLength(5);
  });
});
