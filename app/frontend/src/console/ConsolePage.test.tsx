import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { detected, events, resolved } from './fixtures';
import { ConsolePage } from './ConsolePage';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(data: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }));
  }
}

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

describe('ConsolePage', () => {
  let detail = detected;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    FakeEventSource.instances = [];
    detail = detected;
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === '/api/scenario/trigger-cancellation') {
        expect(JSON.parse(String(init?.body))).toMatchObject({ flight: 'AC1234' });
        return json({ journey_ids: ['J-1'] });
      }
      if (url === '/api/scenario/reset') return json({ status: 'reset' });
      if (url === '/api/journeys/J-1') return json(detail);
      return json({}, 404);
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('EventSource', FakeEventSource);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('starts idle with trigger and reset controls', () => {
    render(<ConsolePage />);
    expect(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Reset' })).toBeEnabled();
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it('triggers, streams events live and refreshes the journey card', async () => {
    render(<ConsolePage />);
    await userEvent.click(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' }));
    await screen.findByText('Detected');
    const es = FakeEventSource.instances[0]!;
    expect(es.url).toBe('/api/journeys/J-1/events');

    detail = resolved;
    act(() => es.emit(events[0]));
    expect(await screen.findByText('Cancellation detected')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('14A')).toBeInTheDocument());
    act(() => es.emit(events[2]));
    expect(screen.getByText('Net margin is negative')).toBeInTheDocument();
  });

  it('shows an actionable error when the API fails', async () => {
    fetchMock.mockImplementationOnce(() => json({ detail: 'boom' }, 500));
    render(<ConsolePage />);
    await userEvent.click(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not trigger/i);
  });

  it('reset closes the stream and clears the view', async () => {
    render(<ConsolePage />);
    await userEvent.click(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' }));
    await screen.findByText('Detected');
    const es = FakeEventSource.instances[0]!;
    await userEvent.click(screen.getByRole('button', { name: 'Reset' }));
    await waitFor(() => expect(es.closed).toBe(true));
    expect(screen.getByText(/no journey open/i)).toBeInTheDocument();
    expect(screen.getByText(/waiting for events/i)).toBeInTheDocument();
  });
});
