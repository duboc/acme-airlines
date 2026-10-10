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
  let existing: unknown[] = [];
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    FakeEventSource.instances = [];
    detail = detected;
    existing = [];
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === '/api/scenario/trigger-cancellation') {
        expect(JSON.parse(String(init?.body))).toMatchObject({ flight: 'AC1234' });
        return json({ journey_ids: ['J-1'] });
      }
      if (url === '/api/scenario/reset') return json({ status: 'reset' });
      if (url === '/api/journeys') return json(existing);
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
    render(<ConsolePage />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/journeys', expect.anything()));
    fetchMock.mockImplementationOnce(() => json({ detail: 'boom' }, 500));
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

  describe('restoring an active journey', () => {
    const summary = { journey_id: 'J-1', passenger_id: 'P-MARIANA', status: 'RESOLVED' };

    it('loads the current journey on mount and resubscribes to its stream', async () => {
      existing = [summary];
      detail = resolved;
      render(<ConsolePage />);
      expect(await screen.findByText('14A')).toBeInTheDocument();
      expect(FakeEventSource.instances).toHaveLength(1);
      expect(FakeEventSource.instances[0]!.url).toBe('/api/journeys/J-1/events');
      act(() => FakeEventSource.instances[0]!.emit(events[0]));
      expect(await screen.findByText('Cancellation detected')).toBeInTheDocument();
    });

    it('stays idle when there is no journey', async () => {
      render(<ConsolePage />);
      await waitFor(() =>
        expect(fetchMock).toHaveBeenCalledWith('/api/journeys', expect.anything()),
      );
      expect(screen.getByText(/no journey open/i)).toBeInTheDocument();
      expect(FakeEventSource.instances).toHaveLength(0);
    });

    it('stays usable when the journey list cannot be loaded', async () => {
      fetchMock.mockImplementationOnce(() => json({}, 500));
      render(<ConsolePage />);
      expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i);
      expect(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' })).toBeEnabled();
    });

    it('disables Trigger while a journey is open and explains why', async () => {
      existing = [summary];
      detail = resolved;
      render(<ConsolePage />);
      await screen.findByText('14A');
      const trigger = screen.getByRole('button', { name: 'Trigger AC1234 cancellation' });
      expect(trigger).toBeDisabled();
      expect(screen.getByText(/reset to run it again/i)).toBeInTheDocument();
    });

    it('re-enables Trigger after Reset', async () => {
      existing = [summary];
      detail = resolved;
      render(<ConsolePage />);
      await screen.findByText('14A');
      await userEvent.click(screen.getByRole('button', { name: 'Reset' }));
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' })).toBeEnabled(),
      );
    });

    it('attaches to the existing journey when Trigger returns no new journey', async () => {
      fetchMock.mockImplementation((url: string) => {
        if (url === '/api/scenario/trigger-cancellation') return json({ journey_ids: [] });
        if (url === '/api/journeys') return json(existing);
        if (url === '/api/journeys/J-1') return json(resolved);
        return json({}, 404);
      });
      render(<ConsolePage />);
      await waitFor(() =>
        expect(fetchMock).toHaveBeenCalledWith('/api/journeys', expect.anything()),
      );
      existing = [summary];
      await userEvent.click(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' }));
      expect(await screen.findByText('14A')).toBeInTheDocument();
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(FakeEventSource.instances).toHaveLength(1);
    });
  });
});
