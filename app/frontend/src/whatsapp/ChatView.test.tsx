import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from './ChatView';
import { ApiHttpError, formatTransientError, type WhatsAppMessage } from './api';

const RESOLUTION =
  'AC1234 is cancelled. You are rebooked on AC1250 at 06:40, hotel Aeroporto Inn is booked, and a meal voucher of $25 is in your wallet.';

let transcript: WhatsAppMessage[];
let calls: { url: string; method: string; body?: unknown }[];

beforeEach(() => {
  calls = [];
  transcript = [
    {
      message_id: 'm1',
      direction: 'outbound',
      text: RESOLUTION,
      timestamp: '2026-10-10T21:05:00Z',
      buttons: [],
    },
    {
      message_id: 'm2',
      direction: 'outbound',
      text: 'Optional: an upgrade is available if you would like it.',
      timestamp: '2026-10-10T21:06:00Z',
      buttons: ['Accept upgrade $135', 'No thanks'],
    },
  ];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, method, body });
      if (method === 'POST' && url.endsWith('/messages')) {
        const msg = {
          message_id: `n${transcript.length}`,
          direction: 'inbound' as const,
          text: body.text,
          timestamp: '2026-10-10T21:07:00Z',
        };
        transcript.push(msg);
        return new Response(JSON.stringify(msg), { status: 201 });
      }
      if (method === 'POST') {
        return new Response(JSON.stringify({ offer_status: 'accepted' }), { status: 200 });
      }
      return new Response(JSON.stringify(transcript), { status: 200 });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe('ChatView', () => {
  it('renders the resolution message with all facts and timestamps', async () => {
    render(<ChatView passengerId="P-MARIANA" />);
    expect(await screen.findByText(RESOLUTION)).toBeInTheDocument();
    expect(calls[0]?.url).toBe('/api/whatsapp/P-MARIANA/messages');
    expect(screen.getAllByText(/^\d\d:\d\d$/).length).toBe(2);
    expect(screen.getByText(/times in UTC/)).toBeInTheDocument();
  });

  it('sends a typed reply and shows it in the transcript', async () => {
    render(<ChatView passengerId="P-MARIANA" />);
    await screen.findByText(RESOLUTION);
    await userEvent.type(screen.getByLabelText('Message'), 'Can I leave after 10am?');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('Can I leave after 10am?')).toBeInTheDocument();
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.body).toEqual({ text: 'Can I leave after 10am?' });
    expect(screen.getByLabelText('Message')).toHaveValue('');
  });

  it('posts an offer action once and disables the buttons', async () => {
    render(<ChatView passengerId="P-MARIANA" />);
    const accept = await screen.findByRole('button', { name: 'Accept upgrade $135' });
    await userEvent.click(accept);
    await userEvent.click(accept);
    await waitFor(() => expect(accept).toBeDisabled());
    expect(screen.getByRole('button', { name: 'No thanks' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '✓ Accept upgrade $135' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByText('Optional. You can ignore this.')).toBeInTheDocument();
    const posts = calls.filter((c) => c.url.endsWith('/actions'));
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({ action: 'accept_offer' });
  });

  it('maps a decline button to decline_offer', async () => {
    render(<ChatView passengerId="P-MARIANA" />);
    await userEvent.click(await screen.findByRole('button', { name: 'No thanks' }));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/actions'))?.body).toEqual({
        action: 'decline_offer',
      }),
    );
  });

  it('renders human-readable attachment chips on outbound messages', async () => {
    transcript = [
      {
        message_id: 'm1',
        direction: 'outbound',
        text: RESOLUTION,
        timestamp: '2026-10-10T21:05:00Z',
        attachments: ['hotel_voucher', 'meal_voucher'],
      },
      {
        message_id: 'm2',
        direction: 'outbound',
        text: 'Seat 2A confirmed.',
        timestamp: '2026-10-10T21:08:00Z',
        attachments: ['boarding_pass', 'hotel_voucher'],
      },
    ];
    render(<ChatView passengerId="P-MARIANA" />);
    expect(await screen.findByText('Meal voucher')).toBeInTheDocument();
    expect(screen.getByText('Boarding pass')).toBeInTheDocument();
    expect(screen.getAllByText('Hotel voucher')).toHaveLength(2);
  });

  it('notifies onChanged after sending a reply or tapping an offer button', async () => {
    const onChanged = vi.fn();
    render(<ChatView passengerId="P-MARIANA" onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Accept upgrade $135' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));

    await userEvent.type(screen.getByLabelText('Message'), 'Can I leave after 10am?');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
  });

  it('disables offer buttons once a newer message supersedes the offer', async () => {
    transcript.push({
      message_id: 'm3',
      direction: 'outbound',
      text: 'This offer is no longer available after your flight change.',
      timestamp: '2026-10-10T21:07:00Z',
    });
    render(<ChatView passengerId="P-MARIANA" />);
    expect(await screen.findByRole('button', { name: 'Accept upgrade $135' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'No thanks' })).toBeDisabled();
  });

  it('clears answered button state when messages reset to empty', async () => {
    render(<ChatView passengerId="P-MARIANA" pollMs={40} />);
    const accept = await screen.findByRole('button', { name: 'Accept upgrade $135' });
    await userEvent.click(accept);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '✓ Accept upgrade $135' })).toBeDisabled(),
    );

    const saved = [...transcript];
    transcript = [];
    await screen.findByText('No messages yet.');

    transcript = saved;
    const restored = await screen.findByRole('button', { name: 'Accept upgrade $135' });
    expect(restored).toBeEnabled();
  });

  it('shows an error when the service is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('x', { status: 500 })),
    );
    render(<ChatView passengerId="P-MARIANA" />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/cannot reach/i);
  });

  it('surfaces 429 Retry-After feedback and pauses background polling during cooldown', async () => {
    let pollCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        pollCalls += 1;
        return new Response(JSON.stringify({ detail: 'Quota exhausted.' }), {
          status: 429,
          headers: { 'Retry-After': '20' },
        });
      }),
    );
    render(<ChatView passengerId="P-MARIANA" pollMs={30} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Engine busy (429): Quota exhausted. Retry in 20s.',
    );
    expect(pollCalls).toBe(1);
    await new Promise((r) => setTimeout(r, 100));
    expect(pollCalls).toBe(1);
  });

  it('disables offer buttons and sets aria-pressed when offerStatus is settled (declined, accepted, invalidated) (#402, #431)', async () => {
    const { rerender } = render(<ChatView passengerId="P-MARIANA" offerStatus="sent" />);
    const accept = await screen.findByRole('button', { name: 'Accept upgrade $135' });
    const decline = screen.getByRole('button', { name: 'No thanks' });
    expect(accept).toBeEnabled();
    expect(decline).toBeEnabled();

    rerender(<ChatView passengerId="P-MARIANA" offerStatus="declined" />);
    const declinedBtn = screen.getByRole('button', { name: '✓ No thanks' });
    expect(declinedBtn).toBeDisabled();
    expect(declinedBtn).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Accept upgrade $135' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Accept upgrade $135' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );

    rerender(<ChatView passengerId="P-MARIANA" offerStatus="accepted" />);
    const acceptedBtn = screen.getByRole('button', { name: '✓ Accept upgrade $135' });
    expect(acceptedBtn).toBeDisabled();
    expect(acceptedBtn).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'No thanks' })).toBeDisabled();

    rerender(<ChatView passengerId="P-MARIANA" offerStatus="invalidated" />);
    const invAccept = screen.getByRole('button', { name: 'Accept upgrade $135' });
    const invDecline = screen.getByRole('button', { name: 'No thanks' });
    expect(invAccept).toBeDisabled();
    expect(invDecline).toBeDisabled();
    expect(invAccept).toHaveAttribute('aria-pressed', 'false');
    expect(invDecline).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText('Offer no longer available.')).toBeInTheDocument();
    expect(screen.queryByText('Optional. You can ignore this.')).not.toBeInTheDocument();
  });

  it('does not mark Accept as chosen and shows Offer no longer available when postAction returns offer_status invalidated (#431 AC1)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET';
        calls.push({ url, method });
        if (method === 'POST' && url.endsWith('/actions')) {
          return new Response(JSON.stringify({ offer_status: 'invalidated' }), { status: 200 });
        }
        return new Response(JSON.stringify(transcript), { status: 200 });
      }),
    );
    render(<ChatView passengerId="P-MARIANA" />);
    const accept = await screen.findByRole('button', { name: 'Accept upgrade $135' });
    await userEvent.click(accept);

    await waitFor(() => expect(accept).toBeDisabled());
    const decline = screen.getByRole('button', { name: 'No thanks' });
    expect(decline).toBeDisabled();
    expect(accept).toHaveAttribute('aria-pressed', 'false');
    expect(decline).toHaveAttribute('aria-pressed', 'false');
    expect(accept.textContent).not.toMatch(/^✓ /);
    expect(decline.textContent).not.toMatch(/^✓ /);
    expect(screen.getByText('Offer no longer available.')).toBeInTheDocument();
    expect(screen.queryByText('Optional. You can ignore this.')).not.toBeInTheDocument();
  });

  it('keeps offer buttons disabled without marking chosen, calls refresh and onChanged, and surfaces err.detail on 409 Conflict (#431 AC2)', async () => {
    const onChanged = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET';
        calls.push({ url, method });
        if (method === 'POST' && url.endsWith('/actions')) {
          return new Response(
            JSON.stringify({
              code: 'no_offer_sent',
              detail: 'no offer has been sent to this passenger',
            }),
            { status: 409 },
          );
        }
        return new Response(JSON.stringify(transcript), { status: 200 });
      }),
    );
    render(<ChatView passengerId="P-MARIANA" onChanged={onChanged} pollMs={60_000} />);
    const decline = await screen.findByRole('button', { name: 'No thanks' });
    const getCallsBefore = calls.filter((c) => c.method === 'GET').length;

    await userEvent.click(decline);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'no offer has been sent to this passenger',
    );
    const accept = screen.getByRole('button', { name: 'Accept upgrade $135' });
    const declineAfter = screen.getByRole('button', { name: 'No thanks' });
    expect(accept).toBeDisabled();
    expect(declineAfter).toBeDisabled();
    expect(accept).toHaveAttribute('aria-pressed', 'false');
    expect(declineAfter).toHaveAttribute('aria-pressed', 'false');
    expect(calls.filter((c) => c.method === 'GET').length).toBeGreaterThan(getCallsBefore);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('treats 502 and 504 as transient errors and clears optimistic answer so the user can retry (#431 AC3)', async () => {
    for (const status of [429, 502, 503, 504]) {
      const err = new ApiHttpError(status, 'Gateway timeout.', 5);
      expect(err.isTransient).toBe(true);
      expect(formatTransientError(err, 'fallback')).toBe(
        `Engine busy (${status}): Gateway timeout. Retry in 5s.`,
      );
      const emptyErr = new ApiHttpError(status, '', null);
      expect(emptyErr.isTransient).toBe(true);
      expect(formatTransientError(emptyErr, 'fallback')).toBe(
        `Engine busy (${status}): Engine is temporarily busy.`,
      );
    }

    let postCount = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if ((init?.method ?? 'GET') === 'POST' && url.endsWith('/actions')) {
          postCount += 1;
          if (postCount === 1) {
            return new Response(JSON.stringify({ detail: 'Bad gateway.' }), { status: 502 });
          }
          return new Response(JSON.stringify({ offer_status: 'accepted' }), { status: 200 });
        }
        return new Response(JSON.stringify(transcript), { status: 200 });
      }),
    );
    render(<ChatView passengerId="P-MARIANA" pollMs={60_000} />);
    const accept = await screen.findByRole('button', { name: 'Accept upgrade $135' });
    await userEvent.click(accept);

    expect(await screen.findByRole('alert')).toHaveTextContent('Engine busy (502): Bad gateway.');
    expect(accept).toBeEnabled();
    expect(accept).toHaveAttribute('aria-pressed', 'false');

    await userEvent.click(accept);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '✓ Accept upgrade $135' })).toBeDisabled(),
    );
  });

  it('refreshes immediately when refreshToken increments unless in cooldown (#402)', async () => {
    const { rerender } = render(
      <ChatView passengerId="P-MARIANA" pollMs={60_000} refreshToken={0} />,
    );
    expect(await screen.findByText(RESOLUTION)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'GET')).toHaveLength(1);

    transcript = [
      ...transcript,
      {
        message_id: 'm-new',
        direction: 'outbound',
        text: 'Replanned on AC1252.',
        timestamp: '2026-10-10T21:08:00Z',
      },
    ];
    rerender(<ChatView passengerId="P-MARIANA" pollMs={60_000} refreshToken={1} />);
    expect(await screen.findByText('Replanned on AC1252.')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'GET')).toHaveLength(2);
  });
});
