import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AgentsBody } from './panels/AgentsPanel';
import { GithubBody } from './panels/GithubPanel';
import { SpendBody } from './panels/SpendPanel';
import { ScrollRegion } from './ScrollRegion';

const env = { state: 'ok', as_of: '2026-10-10T12:00:00Z', message: null } as const;
const zero = { date: 'total', vm: 0, cloud_run: 0, vertex_ai: 0, other: 0, total: 0 };

function expectFocusableRegion(name: string) {
  const el = screen.getByRole('region', { name });
  expect(el).toHaveAttribute('tabindex', '0');
  expect(el.querySelector('table')).not.toBeNull();
}

describe('scrollable regions', () => {
  it('ScrollRegion is a named, focusable region', () => {
    render(<ScrollRegion label="x">y</ScrollRegion>);
    expect(screen.getByRole('region', { name: 'x' })).toHaveAttribute('tabindex', '0');
  });
  it('wraps the spend, agents and PR tables', () => {
    const { unmount } = render(
      <SpendBody data={{ ...env, days: 30, currency: 'USD', series: [], totals: zero }} />,
    );
    expectFocusableRegion('Daily spend table, scrollable');
    unmount();
    const a = render(
      <AgentsBody
        data={{ ...env, lead_last_activity: null, limits_exceeded_count: 0, agents: [] }}
      />,
    );
    expectFocusableRegion('Agents table, scrollable');
    a.unmount();
    render(
      <GithubBody
        data={{
          ...env,
          merges_per_day: [],
          snapshot: {
            ts: env.as_of,
            issues_by_status: [],
            issues_by_label: [],
            merges_24h: 0,
            ci_conclusions: [],
            ci_pass_rate: null,
            open_prs: [
              {
                number: 1,
                title: 't',
                author: 'a',
                created_at: env.as_of,
                latest_verdict: null,
                ci_conclusion: null,
              },
            ],
          },
        }}
      />,
    );
    expectFocusableRegion('Open pull requests table, scrollable');
  });
  it('does not make static panels focusable', async () => {
    render(
      <AgentsBody
        data={{ ...env, lead_last_activity: null, limits_exceeded_count: 0, agents: [] }}
      />,
    );
    expect(document.querySelectorAll('section[tabindex]')).toHaveLength(0);
  });
});
