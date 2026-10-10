import { describe, expect, it } from 'vitest';
import { base } from './fixtures';
import { evaluate } from './expectations';

describe('evaluate', () => {
  it('has no expectation before the first step', () => {
    expect(evaluate({ ...base, step_index: 0 })).toBeNull();
  });

  it('passes when the observed state matches the step expectation', () => {
    const r = evaluate({
      ...base,
      step_index: 1,
      journey_status: 'RESOLVED',
      flight: 'AC1240',
      seat: '14A',
    });
    expect(r?.text).toMatch(/AC1240/);
    expect(r?.mismatches).toEqual([]);
  });

  it('lists what differs when it does not match', () => {
    const r = evaluate({ ...base, step_index: 1, journey_status: 'DETECTED', flight: 'AC1240' });
    expect(r?.mismatches).toEqual([
      'status: expected RESOLVED, saw DETECTED',
      'seat: expected 14A, saw none',
    ]);
  });

  it('checks the distinctive final outcome of each preset', () => {
    const final = (id: string, steps: number, over: object) =>
      evaluate({ ...base, scenario_id: id, step_index: steps, done: true, ...over });
    expect(
      final('happy-path', 5, {
        journey_status: 'CLOSED',
        flight: 'AC1252',
        seat: '2A',
        charged: '135.00',
        offer_status: 'accepted',
      })?.mismatches,
    ).toEqual([]);
    expect(
      final('declines-upgrade', 5, { flight: 'AC1252', seat: '14A', offer_status: 'declined' })
        ?.mismatches,
    ).toEqual([]);
    expect(
      final('seat-taken', 2, { flight: 'AC1240', seat: '15A', journey_status: 'RESOLVED' })
        ?.mismatches,
    ).toEqual([]);
    expect(
      final('hotel-unavailable', 1, { needs_human: true, journey_status: 'RESOLVING' })?.mismatches,
    ).toEqual([]);
    expect(
      final('upgrade-race', 6, { offer_status: 'invalidated', charged: '0.00', seat: '14A' })
        ?.mismatches,
    ).toEqual([]);
    expect(
      final('upgrade-race', 6, { offer_status: 'accepted', charged: '135.00', seat: '2A' })
        ?.mismatches.length,
    ).toBeGreaterThan(0);
  });

  it('returns null for unknown presets', () => {
    expect(evaluate({ ...base, scenario_id: 'nope', step_index: 1 })).toBeNull();
  });
});
