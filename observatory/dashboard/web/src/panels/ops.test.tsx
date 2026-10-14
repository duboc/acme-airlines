import { render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

const env = { state: 'ok', as_of: '2026-10-10T12:00:00Z', message: null };
const agent = (name: string, role: string, extra = {}) => ({
  name,
  template: name,
  role,
  tier: 'sonnet',
  phase: 'running',
  activity: 'executing',
  container_status: 'running',
  started_at: '2026-10-10T10:30:00Z',
  last_activity: '2026-10-10T11:55:00Z',
  harness: 'claude',
  model: 'm',
  limits_exceeded: false,
  running_seconds: 5400,
  ...extra,
});
const agents = (lead: string | null) => ({
  ...env,
  lead_last_activity: lead,
  limits_exceeded_count: 1,
  agents: [
    agent('acme-backend', 'backend', { limits_exceeded: true }),
    agent('acme-lead', 'lead', { phase: 'stopped', running_seconds: 600 }),
  ],
});
const pr = (n: number, v: string | null) => ({
  number: n,
  title: `PR ${n}`,
  author: 'a',
  created_at: '2026-10-10T11:00:00Z',
  latest_verdict: v,
  ci_conclusion: 'success',
});
const github = {
  ...env,
  snapshot: {
    ts: env.as_of,
    issues_by_status: [{ key: 'ready', count: 3 }],
    issues_by_label: [],
    open_prs: [pr(114, 'REQUEST CHANGES'), pr(115, null)],
    merges_24h: 6,
    ci_conclusions: [],
    ci_pass_rate: 0.875,
  },
  merges_per_day: [{ date: '2026-10-09', count: 3 }],
};
const zero = { date: 'total', vm: 0, cloud_run: 0, vertex_ai: 0, other: 0, total: 0 };

function mock(over: Record<string, unknown> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const name = url.replace('/api/', '').split('?')[0]!;
      const base: Record<string, unknown> = {
        spend: { ...env, days: 30, series: [], totals: zero },
        models: { ...env, hours: 24, models: [] },
        budget: { ...env, budget_usd: 1, mtd_usd: 0, percent: 0, markers: [], guard_actions: [] },
        agents: agents('2026-10-10T11:58:00Z'),
        github,
        'agent-engine': { ...env, hours: 1, engines: [] },
        irrops: { ...env, hours: 24, guardrail: [], demo_health: 'unknown' },
        'collector-health': {
          ...env,
          healthy: true,
          snapshot_age_seconds: 30,
          last_execution: null,
        },
      };
      return { ok: true, status: 200, json: async () => over[name] ?? base[name] };
    }),
  );
}
afterEach(() => vi.unstubAllGlobals());

describe('Agents', () => {
  it('groups by role with phase, duration and a text LIMITS_EXCEEDED flag', async () => {
    mock();
    render(<App />);
    const p = screen.getByRole('region', { name: 'Agents' });
    const row = await within(p).findByRole('row', { name: /acme-backend/ });
    expect(row).toHaveTextContent('LIMITS_EXCEEDED');
    expect(row).toHaveTextContent('running');
    expect(row).toHaveTextContent('1h 30m');
    expect(within(p).getByRole('row', { name: /acme-lead/ })).toHaveTextContent('stopped');
    expect(within(p).getByText('backend')).toBeInTheDocument();
    expect(within(p).getByText('Lead last active 2m ago')).toBeInTheDocument();
  });
  it('marks a stale lead in words', async () => {
    mock({ agents: agents('2026-10-10T10:00:00Z') });
    render(<App />);
    const p = screen.getByRole('region', { name: 'Agents' });
    expect(await within(p).findByText(/Stale: lead last active 2h 0m ago/)).toBeInTheDocument();
  });
});

describe('GitHub', () => {
  it('shows the latest verdict, CI rate and merges', async () => {
    mock();
    render(<App />);
    const p = screen.getByRole('region', { name: 'GitHub' });
    expect(await within(p).findByRole('row', { name: /#114/ })).toHaveTextContent(
      'REQUEST CHANGES',
    );
    expect(within(p).getByRole('row', { name: /#115/ })).toHaveTextContent('No verdict');
    expect(within(p).getByText('88%')).toBeInTheDocument();
    expect(within(p).getByText(/ready:/)).toHaveTextContent('ready: 3');
    expect(within(p).getByRole('row', { name: /2026-10-09/ })).toHaveTextContent('3');
  });
  it('says the token is not configured when empty', async () => {
    mock({ github: { ...env, state: 'empty', as_of: null, snapshot: null, merges_per_day: [] } });
    render(<App />);
    const p = screen.getByRole('region', { name: 'GitHub' });
    expect(await within(p).findByRole('status')).toHaveTextContent(
      "GitHub token isn't configured yet",
    );
  });
});
