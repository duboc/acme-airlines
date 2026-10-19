import type { ScenarioState } from './types';

interface Check {
  status?: string;
  notStatus?: string;
  flight?: string;
  seat?: string;
  seatReason?: boolean;
  offer?: string | null;
  offersSent?: number;
  charged?: string;
  needsHuman?: boolean;
}
interface Expected {
  text: string;
  check: Check;
}
export interface Evaluation {
  text: string;
  mismatches: string[];
}

const REBOOKED: Expected = {
  text: 'Rebooked on AC1240 with seat 14A, hotel and vouchers. Sales holds back.',
  check: { status: 'RESOLVED', flight: 'AC1240', seat: '14A', offersSent: 0 },
};
const AC1252: Expected = {
  text: 'Replanned to AC1252 (after 10am), still seat 14A.',
  check: { status: 'RESOLVED', flight: 'AC1252', seat: '14A', offersSent: 0 },
};
const OFFER: Expected = {
  text: 'Only now, after her acknowledgement: one optional upgrade offer is sent.',
  check: { status: 'RESOLVED', offer: 'sent', offersSent: 1, charged: '0.00' },
};
const START = (text: string): Expected[] => [
  { text, check: { status: 'RESOLVED', offersSent: 0 } },
];

// Expected state after step N (index N-1) of each preset, as the tester should see it.
const TABLE: Record<string, Expected[]> = {
  'happy-path': [
    REBOOKED,
    REBOOKED,
    AC1252,
    OFFER,
    {
      text: 'Journey CLOSED on AC1252, seat 2A, $135.00 charged once.',
      check: {
        status: 'CLOSED',
        flight: 'AC1252',
        seat: '2A',
        charged: '135.00',
        offer: 'accepted',
        offersSent: 1,
      },
    },
  ],
  'declines-upgrade': [
    REBOOKED,
    REBOOKED,
    AC1252,
    OFFER,
    {
      text: 'Offer declined: keeps 14A on AC1252, nothing charged, no further offer.',
      check: {
        status: 'CLOSED',
        flight: 'AC1252',
        seat: '14A',
        charged: '0.00',
        offer: 'declined',
        offersSent: 1,
      },
    },
  ],
  'unsatisfiable-request': [
    ...START('Rebooked on AC1240.'),
    ...START('Resolution message sent.'),
    {
      text: 'An honest message: after 1pm is not possible. She stays on AC1240; no offer.',
      check: { status: 'RESOLVED', flight: 'AC1240', offer: null, offersSent: 0 },
    },
  ],
  'seat-taken': [
    {
      text: '14A is taken: nearest window seat 15A on AC1240, with the reason recorded.',
      check: { status: 'RESOLVED', flight: 'AC1240', seat: '15A', seatReason: true, offersSent: 0 },
    },
    {
      text: 'Resolution message explains the seat change.',
      check: { status: 'RESOLVED', flight: 'AC1240', seat: '15A', seatReason: true, offersSent: 0 },
    },
  ],
  'hotel-unavailable': [
    {
      text: 'No hotel room: handed to a human. Never marked resolved, no offer.',
      check: {
        status: 'RESOLVING',
        notStatus: 'RESOLVED',
        flight: 'AC1240',
        seat: '14A',
        needsHuman: true,
        offer: null,
        offersSent: 0,
        charged: '0.00',
      },
    },
    {
      text: 'Still with a human. No offer, no charge.',
      check: {
        status: 'RESOLVING',
        notStatus: 'RESOLVED',
        flight: 'AC1240',
        seat: '14A',
        needsHuman: true,
        offer: null,
        offersSent: 0,
        charged: '0.00',
      },
    },
  ],
  'upgrade-race': [
    REBOOKED,
    REBOOKED,
    AC1252,
    OFFER,
    {
      text: 'Replanning invalidates the open offer.',
      check: {
        status: 'RESOLVED',
        flight: 'AC1252',
        seat: '14A',
        offer: 'invalidated',
        offersSent: 1,
        charged: '0.00',
      },
    },
    {
      text: 'Stale Accept: nothing charged, she keeps seat 14A.',
      check: {
        status: 'RESOLVED',
        flight: 'AC1252',
        seat: '14A',
        offer: 'invalidated',
        offersSent: 1,
        charged: '0.00',
      },
    },
  ],
};

export function evaluate(s: ScenarioState): Evaluation | null {
  const expected = TABLE[s.scenario_id]?.[s.step_index - 1];
  if (!expected) return null;
  const { check: c } = expected;
  const seen = (v: string | null | undefined) => v ?? 'none';
  const mismatches: string[] = [];
  const eq = (name: string, want: string | null | undefined, got: string | null) => {
    if (want !== undefined && want !== got)
      mismatches.push(`${name}: expected ${seen(want)}, saw ${seen(got)}`);
  };
  eq('status', c.status, s.journey_status);
  if (c.notStatus !== undefined && c.notStatus === s.journey_status)
    mismatches.push(`status: must not be ${c.notStatus}`);
  eq('flight', c.flight, s.flight);
  eq('seat', c.seat, s.seat);
  if (c.seatReason && !s.seat_reason)
    mismatches.push('seat_reason: expected reason recorded, saw none');
  eq('offer', c.offer, s.offer_status);
  if (c.offersSent !== undefined && c.offersSent !== s.offers_sent)
    mismatches.push(`offers_sent: expected ${c.offersSent}, saw ${s.offers_sent}`);
  eq('charged', c.charged, s.charged);
  if (c.needsHuman !== undefined && c.needsHuman !== s.needs_human)
    mismatches.push(`needs human: expected ${c.needsHuman ? 'yes' : 'no'}`);
  return { text: expected.text, mismatches };
}

export function applyExpectedStep(prev: ScenarioState, steps?: readonly string[]): ScenarioState {
  const nextIndex = prev.step_index + 1;
  const c = TABLE[prev.scenario_id]?.[nextIndex - 1]?.check;
  const nextStatus =
    c?.status !== undefined
      ? c.status
      : c?.notStatus !== undefined && prev.journey_status === c.notStatus
        ? null
        : prev.journey_status;
  return {
    ...prev,
    step_index: nextIndex,
    done: nextIndex >= prev.total_steps,
    last_step: steps?.[prev.step_index] ?? prev.next_step ?? prev.last_step,
    next_step: steps?.[nextIndex] ?? null,
    journey_status: nextStatus,
    flight: c?.flight !== undefined ? c.flight : prev.flight,
    seat: c?.seat !== undefined ? c.seat : prev.seat,
    seat_reason: c?.seatReason
      ? prev.seat_reason ||
        '14A unavailable on AC1240; assigned nearest window seat 15A as a diamond tier courtesy'
      : prev.seat_reason,
    offers_sent:
      c?.offersSent !== undefined
        ? c.offersSent
        : c?.offer === 'sent'
          ? Math.max(prev.offers_sent, 1)
          : prev.offers_sent,
    offer_status: c?.offer !== undefined ? c.offer : prev.offer_status,
    charged: c?.charged !== undefined ? c.charged : prev.charged,
    needs_human: c?.needsHuman !== undefined ? c.needsHuman : prev.needs_human,
    last_message: null,
  };
}
