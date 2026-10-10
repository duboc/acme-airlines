import type { ScenarioState } from './types';

interface Check {
  status?: string;
  notStatus?: string;
  flight?: string;
  seat?: string;
  offer?: string;
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
  check: { status: 'RESOLVED', flight: 'AC1240', seat: '14A' },
};
const AC1252: Expected = {
  text: 'Replanned to AC1252 (after 10am), still seat 14A.',
  check: { status: 'RESOLVED', flight: 'AC1252', seat: '14A' },
};
const OFFER: Expected = {
  text: 'Only now, after her acknowledgement: one optional upgrade offer is sent.',
  check: { status: 'RESOLVED', offer: 'sent', charged: '0.00' },
};
const START = (text: string): Expected[] => [{ text, check: { status: 'RESOLVED' } }];

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
      check: { flight: 'AC1252', seat: '14A', charged: '0.00', offer: 'declined' },
    },
  ],
  'unsatisfiable-request': [
    ...START('Rebooked on AC1240.'),
    ...START('Resolution message sent.'),
    {
      text: 'An honest message: after 1pm is not possible. She stays on AC1240; no offer.',
      check: { status: 'RESOLVED', flight: 'AC1240' },
    },
  ],
  'seat-taken': [
    {
      text: '14A is taken: nearest window seat 15A on AC1240, with the reason recorded.',
      check: { status: 'RESOLVED', flight: 'AC1240', seat: '15A' },
    },
    {
      text: 'Resolution message explains the seat change.',
      check: { status: 'RESOLVED', flight: 'AC1240', seat: '15A' },
    },
  ],
  'hotel-unavailable': [
    {
      text: 'No hotel room: handed to a human. Never marked resolved, no offer.',
      check: { notStatus: 'RESOLVED', needsHuman: true },
    },
    {
      text: 'Still with a human. No offer, no charge.',
      check: { notStatus: 'RESOLVED', needsHuman: true, charged: '0.00' },
    },
  ],
  'upgrade-race': [
    REBOOKED,
    REBOOKED,
    AC1252,
    OFFER,
    { text: 'Replanning invalidates the open offer.', check: { offer: 'invalidated' } },
    {
      text: 'Stale Accept: nothing charged, she keeps seat 14A.',
      check: { offer: 'invalidated', charged: '0.00', seat: '14A' },
    },
  ],
};

export function evaluate(s: ScenarioState): Evaluation | null {
  const expected = TABLE[s.scenario_id]?.[s.step_index - 1];
  if (!expected) return null;
  const { check: c } = expected;
  const seen = (v: string | null | undefined) => v ?? 'none';
  const mismatches: string[] = [];
  const eq = (name: string, want: string | undefined, got: string | null) => {
    if (want !== undefined && want !== got)
      mismatches.push(`${name}: expected ${want}, saw ${seen(got)}`);
  };
  eq('status', c.status, s.journey_status);
  if (c.notStatus !== undefined && c.notStatus === s.journey_status)
    mismatches.push(`status: must not be ${c.notStatus}`);
  eq('flight', c.flight, s.flight);
  eq('seat', c.seat, s.seat);
  eq('offer', c.offer, s.offer_status);
  eq('charged', c.charged, s.charged);
  if (c.needsHuman !== undefined && c.needsHuman !== s.needs_human)
    mismatches.push(`needs human: expected ${c.needsHuman ? 'yes' : 'no'}`);
  return { text: expected.text, mismatches };
}
