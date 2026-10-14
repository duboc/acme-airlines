import { render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentEnginePanel } from './AgentEnginePanel';

const env = { state: 'ok', as_of: '2026-10-10T12:00:30Z', message: null, hours: 1 };
const trace = {
  trace_id: '4bf9',
  name: 'invoke_agent',
  start_time: '2026-10-10T11:58:00Z',
  duration_ms: 1830.5,
  status: 'ok',
  console_url: 'https://console.cloud.google.com/traces/list?project=p&tid=4bf9',
};
const engine = (name: string, extra = {}) => ({
  name,
  engine_id: '1',
  requests: 200,
  errors: 10,
  error_rate: 0.05,
  latency_p50_ms: 850,
  latency_p95_ms: 2400,
  trace_access: 'granted',
  traces: [trace],
  ...extra,
});

function mock(body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      if (body instanceof Error) throw body;
      return { ok: true, status: 200, json: async () => body };
    }),
  );
}
afterEach(() => vi.unstubAllGlobals());
const panel = () => screen.getByRole('region', { name: 'Agent Engine' });

describe('AgentEnginePanel', () => {
  it('shows loading first', () => {
    mock({ ...env, engines: [] });
    render(<AgentEnginePanel />);
    expect(within(panel()).getByText('Loading…')).toBeInTheDocument();
  });

  it('renders metrics and trace links per engine', async () => {
    mock({ ...env, engines: [engine('acme-customer-agent'), engine('acme-sales-agent')] });
    render(<AgentEnginePanel />);
    expect(await within(panel()).findByText('acme-customer-agent')).toBeInTheDocument();
    expect(within(panel()).getByText('acme-sales-agent')).toBeInTheDocument();
    const row = within(panel()).getAllByRole('article')[0]!;
    expect(within(row).getByText('850 ms')).toBeInTheDocument();
    expect(within(row).getByText('2.4 s')).toBeInTheDocument();
    expect(within(row).getByText('5.0%')).toBeInTheDocument();
    expect(within(row).getByText('200')).toBeInTheDocument();
    const link = within(row).getByRole('link', { name: /4bf9/ });
    expect(link.getAttribute('href')).toMatch(/^https:\/\/console\.cloud\.google\.com\/traces/);
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });

  it('shows metrics and a pending note when trace access is not granted', async () => {
    mock({
      ...env,
      engines: [engine('acme-customer-agent', { trace_access: 'not_granted', traces: null })],
    });
    render(<AgentEnginePanel />);
    expect(await within(panel()).findByText('850 ms')).toBeInTheDocument();
    expect(within(panel()).getByText('Trace access not granted')).toBeInTheDocument();
    expect(within(panel()).queryByRole('link')).not.toBeInTheDocument();
  });

  it('shows "—" for a null error rate and a note for no traces', async () => {
    mock({
      ...env,
      engines: [
        engine('a', {
          requests: 0,
          errors: 0,
          error_rate: null,
          latency_p50_ms: null,
          latency_p95_ms: null,
          traces: [],
        }),
      ],
    });
    render(<AgentEnginePanel />);
    expect(await within(panel()).findByText('No recent traces.')).toBeInTheDocument();
    expect(within(panel()).getAllByText('—').length).toBeGreaterThanOrEqual(3);
  });

  it('shows the empty state when no engines are configured', async () => {
    mock({ ...env, state: 'empty', engines: [] });
    render(<AgentEnginePanel />);
    expect(await within(panel()).findByText('No data yet')).toBeInTheDocument();
    expect(within(panel()).getByRole('status')).toHaveTextContent('No Agent Engines');
  });

  it('shows the error state when the API fails', async () => {
    mock(new Error('boom'));
    render(<AgentEnginePanel />);
    expect(await within(panel()).findByText('Failed to load')).toBeInTheDocument();
    expect(within(panel()).getByRole('status')).toHaveTextContent('boom');
  });
});
