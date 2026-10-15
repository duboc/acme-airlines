import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { IrropsResponse } from '../api';
import { IrropsPanel } from './IrropsPanel';

const full: IrropsResponse = {
  state: 'ok',
  as_of: '2026-10-10T12:00:00Z',
  message: null,
  hours: 24,
  guardrail: [
    { rule: 'all', outcome: 'approve', count: 6 },
    { rule: 'not_acknowledged', outcome: 'reject', count: 4 },
  ],
  offers_proposed: 7,
  offers_accepted: 4,
  offers_declined: 2,
  template_fallbacks: 3,
  template_fallback_rate: 0.25,
  needs_human: 1,
  notification_count: 12,
  notification_p50_s: 8,
  notification_p95_s: 41.5,
  errors_5xx: 0,
  errors_5xx_per_hour: 0,
  engine_rebuilds: 2,
  engine_latency_p50_ms: 150.0,
  engine_latency_p95_ms: 400.0,
  demo_health: 'healthy',
  last_metric_at: '2026-10-10T12:00:00Z',
};
const pending: IrropsResponse = {
  ...full,
  guardrail: [],
  offers_proposed: null,
  offers_accepted: null,
  offers_declined: null,
  template_fallbacks: null,
  template_fallback_rate: null,
  needs_human: null,
  notification_count: null,
  notification_p50_s: null,
  notification_p95_s: null,
  errors_5xx: null,
  errors_5xx_per_hour: null,
  engine_rebuilds: null,
  engine_latency_p50_ms: null,
  engine_latency_p95_ms: null,
  demo_health: 'unknown',
};

function mock(body: unknown) {
  const fn = vi.fn(async () => ({ ok: true, status: 200, json: async () => body }));
  vi.stubGlobal('fetch', fn);
  return fn;
}
afterEach(() => vi.unstubAllGlobals());
const panel = () => screen.getByRole('region', { name: 'IRROPS product' });

describe('IrropsPanel', () => {
  it('shows every metric group', async () => {
    mock(full);
    render(<IrropsPanel />);
    const p = within(panel());
    expect(await p.findByText('Healthy')).toBeInTheDocument();
    expect(p.getByRole('row', { name: /not_acknowledged.*Rejected.*4/ })).toBeInTheDocument();
    expect(p.getByRole('row', { name: /all.*Approved.*6/ })).toBeInTheDocument();
    expect(p.getByText('Offers proposed').nextElementSibling).toHaveTextContent('7');
    expect(p.getByText('Accepted').nextElementSibling).toHaveTextContent('4');
    expect(p.getByText('Declined').nextElementSibling).toHaveTextContent('2');
    expect(p.getByText('Template fallback rate').nextElementSibling).toHaveTextContent('25%');
    expect(p.getByText('Needs human').nextElementSibling).toHaveTextContent('1');
    expect(p.getByText('Engine rebuilds').nextElementSibling).toHaveTextContent('2');
    expect(p.getByText('Engine latency p50').nextElementSibling).toHaveTextContent('150 ms');
    expect(p.getByText('Engine latency p95').nextElementSibling).toHaveTextContent('400 ms');
    expect(p.getByText('Notification p50').nextElementSibling).toHaveTextContent('8.0 s');
    expect(p.getByText('Notification p95').nextElementSibling).toHaveTextContent('41.5 s');
    expect(p.getByText('HTTP 5xx').nextElementSibling).toHaveTextContent('0');
  });

  it('shows pending, not zeros, for metrics without a series', async () => {
    mock(pending);
    render(<IrropsPanel />);
    const p = within(panel());
    expect(await p.findByText('Offers proposed')).toBeInTheDocument();
    expect(p.getByText('Offers proposed').nextElementSibling).toHaveTextContent('No data yet');
    expect(p.getByText('Needs human').nextElementSibling).toHaveTextContent('No data yet');
    expect(p.getByText('Engine rebuilds').nextElementSibling).toHaveTextContent('No data yet');
    expect(p.getByText('Engine latency p50').nextElementSibling).toHaveTextContent('No data yet');
    expect(p.getByText('Engine latency p95').nextElementSibling).toHaveTextContent('No data yet');
    expect(p.getByText('Notification p95').nextElementSibling).toHaveTextContent('No data yet');
    expect(p.getByText('Unknown')).toBeInTheDocument();
    expect(p.getByText('No guardrail decisions yet.')).toBeInTheDocument();
    expect(p.queryByText('0')).not.toBeInTheDocument();
  });

  it('states empty and unavailable explicitly with no numbers', async () => {
    mock({ ...pending, state: 'empty', as_of: null, message: 'No IRROPS metrics yet: x' });
    render(<IrropsPanel />);
    expect(await within(panel()).findByText('No data yet')).toBeInTheDocument();
    expect(within(panel()).getByRole('status')).toHaveTextContent('No IRROPS metrics yet');
    expect(within(panel()).queryByText('Offers proposed')).not.toBeInTheDocument();
  });

  it('keeps data visible and warns when the demo looks down', async () => {
    mock({
      ...full,
      state: 'lagging',
      message: 'No metrics in the last 15 minutes',
      demo_health: 'down',
    });
    render(<IrropsPanel />);
    const p = within(panel());
    expect(await p.findByText('Down')).toBeInTheDocument();
    expect(p.getByText(/Showing the last data received/)).toBeInTheDocument();
    expect(p.getByText('Offers proposed').nextElementSibling).toHaveTextContent('7');
  });

  it('flags degraded health with the 5xx rate in words', async () => {
    mock({ ...full, demo_health: 'degraded', errors_5xx: 6, errors_5xx_per_hour: 0.25 });
    render(<IrropsPanel />);
    const p = within(panel());
    expect(await p.findByText('Degraded')).toBeInTheDocument();
    expect(p.getByText('HTTP 5xx').nextElementSibling).toHaveTextContent('6 (0.25 per hour)');
  });

  it('switches the time range', async () => {
    const fn = mock(full);
    render(<IrropsPanel />);
    await within(panel()).findByText('Healthy');
    await userEvent.click(within(panel()).getByRole('button', { name: '7d' }));
    expect(fn).toHaveBeenLastCalledWith('/api/irrops?hours=168', expect.anything());
    expect(within(panel()).getByRole('button', { name: '7d' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});
