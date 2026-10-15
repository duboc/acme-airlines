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
  emit(data: unknown, lastEventId = '') {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data), lastEventId }));
  }
}

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

describe('ConsolePage', () => {
  let detail = detected;
  let existing: unknown[] = [];
  let journeysStatus = 200;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    FakeEventSource.instances = [];
    detail = detected;
    existing = [];
    journeysStatus = 200;
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === '/api/scenario/trigger-cancellation') {
        expect(JSON.parse(String(init?.body))).toMatchObject({ flight: 'AC1234' });
        return json({ journey_ids: ['J-1'] });
      }
      if (url === '/api/scenario/reset') return json({ status: 'reset' });
      if (url === '/api/scenarios') return json([]);
      if (url.startsWith('/api/whatsapp/')) return json([]);
      if (url === '/api/journeys') return json(existing, journeysStatus);
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

  it('coalesces getJourney calls during an SSE replay burst and deduplicates lastEventId', async () => {
    render(<ConsolePage />);
    await userEvent.click(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' }));
    await screen.findByText('Detected');
    const es = FakeEventSource.instances[0]!;

    let resolveFirst!: (r: Response) => void;
    let journeyCalls = 0;
    fetchMock.mockImplementation((url: string) => {
      if (url === '/api/journeys/J-1') {
        journeyCalls += 1;
        if (journeyCalls === 1) {
          return new Promise<Response>((res) => {
            resolveFirst = res;
          });
        }
        return json(resolved);
      }
      if (url.startsWith('/api/whatsapp/')) return json([]);
      return json({}, 404);
    });

    act(() => {
      for (let i = 1; i <= 16; i += 1) {
        es.emit(events[0], String(i));
      }
      // Duplicate frame with an already-seen SSE id must be ignored.
      es.emit(events[0], '16');
    });

    expect(journeyCalls).toBe(1);
    expect(screen.getAllByRole('listitem')).toHaveLength(5 + 16); // 5 stepper + 16 timeline items

    await act(async () => {
      resolveFirst(new Response(JSON.stringify(detected), { status: 200 }));
    });
    await waitFor(() => expect(journeyCalls).toBe(2));
    await waitFor(() => expect(screen.getByText('14A')).toBeInTheDocument());
  });

  it('shows an actionable error when the API fails', async () => {
    render(<ConsolePage />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/journeys', expect.anything()));
    fetchMock.mockImplementationOnce(() => json({ detail: 'boom' }, 500));
    await userEvent.click(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not trigger/i);
  });

  it('surfaces 429 Retry-After busy feedback and reconnects a closed EventSource', async () => {
    render(<ConsolePage />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/journeys', expect.anything()));
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: 'Quota exhausted.' }), {
          status: 429,
          headers: { 'Retry-After': '20' },
        }),
      ),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Engine busy (429): Quota exhausted. Retry in 20s.',
    );

    await userEvent.click(screen.getByRole('button', { name: 'Trigger AC1234 cancellation' }));
    await screen.findByText('Detected');
    expect(FakeEventSource.instances).toHaveLength(1);
    const firstEs = FakeEventSource.instances[0]! as FakeEventSource & { readyState?: number };
    vi.useFakeTimers();
    try {
      firstEs.readyState = 2;
      act(() => firstEs.onerror?.());
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(FakeEventSource.instances).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
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
      journeysStatus = 500;
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
        if (url === '/api/scenarios') return json([]);
        if (url.startsWith('/api/whatsapp/')) return json([]);
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

  describe('guided scenario wiring', () => {
    const state = {
      scenario_id: 'happy-path',
      step_index: 1,
      total_steps: 5,
      done: false,
      last_step: 'x',
      next_step: 'y',
      journey_id: 'J-1',
      journey_status: 'RESOLVED',
      flight: 'AC1240',
      seat: '14A',
      seat_reason: null,
      offers_sent: 0,
      offer_status: null,
      charged: '0.00',
      needs_human: false,
      last_message: null,
    };

    it('attaches to the journey created by a scenario step and clears it on start', async () => {
      const base = fetchMock.getMockImplementation() as (
        u: string,
        i?: RequestInit,
      ) => Promise<Response>;
      fetchMock.mockImplementation((url: string, init?: RequestInit) => {
        if (url === '/api/scenarios')
          return json([
            {
              id: 'happy-path',
              title: 'Happy path',
              description: 'd',
              steps: ['a'],
              expectation: 'e',
            },
          ]);
        if (url.startsWith('/api/scenarios/')) return json(state);
        return base(url, init);
      });
      detail = resolved;
      render(<ConsolePage />);
      await userEvent.click(await screen.findByRole('button', { name: 'Start scenario' }));
      await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
      expect(await screen.findByText('14A', { selector: 'dd span' })).toBeInTheDocument();
      expect(FakeEventSource.instances).toHaveLength(1);
      await userEvent.click(screen.getByRole('button', { name: 'Start scenario' }));
      await waitFor(() => expect(FakeEventSource.instances[0]!.closed).toBe(true));
      expect(screen.getByText(/no journey open/i)).toBeInTheDocument();
    });

    it('resets DemoPanel Next step and ChatView button state on Reset, and refreshes journey on ChatView actions', async () => {
      const offerMsg = {
        message_id: 'm-offer',
        direction: 'outbound',
        text: 'Upgrade offer',
        timestamp: '2026-10-10T21:06:00Z',
        buttons: ['Accept upgrade $135'],
      };
      const base = fetchMock.getMockImplementation() as (
        u: string,
        i?: RequestInit,
      ) => Promise<Response>;
      fetchMock.mockImplementation((url: string, init?: RequestInit) => {
        if (url === '/api/scenarios')
          return json([
            {
              id: 'happy-path',
              title: 'Happy path',
              description: 'd',
              steps: ['a'],
              expectation: 'e',
            },
          ]);
        if (url.startsWith('/api/scenarios/')) return json(state);
        if (
          url.endsWith('/whatsapp/P-MARIANA/messages') &&
          (!init?.method || init.method === 'GET')
        )
          return json([offerMsg]);
        if (url.endsWith('/whatsapp/P-MARIANA/actions')) {
          detail = {
            ...resolved,
            seat: { flight_number: 'AC1240', seat: '2A', cabin: 'premium_business' },
          };
          return json({ offer_status: 'accepted' });
        }
        return base(url, init);
      });
      detail = resolved;
      render(<ConsolePage />);
      await userEvent.click(await screen.findByRole('button', { name: 'Start scenario' }));
      await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
      expect(await screen.findByText('14A', { selector: 'dd span' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Next step' })).toBeEnabled();

      // Tap offer button in ChatView -> button disables and ConsolePage refreshes JourneyCard to 2A
      const offerBtn = await screen.findByRole('button', { name: 'Accept upgrade $135' });
      await userEvent.click(offerBtn);
      await waitFor(() =>
        expect(screen.getByText('2A', { selector: 'dd span' })).toBeInTheDocument(),
      );
      expect(screen.getByRole('button', { name: '✓ Accept upgrade $135' })).toBeDisabled();

      // Click Reset -> DemoPanel Next step disables and ChatView remounts with fresh enabled button
      await userEvent.click(screen.getByRole('button', { name: 'Reset' }));
      await waitFor(() => expect(screen.getByRole('button', { name: 'Next step' })).toBeDisabled());
      expect(await screen.findByRole('button', { name: 'Accept upgrade $135' })).toBeEnabled();
    });
  });
});
