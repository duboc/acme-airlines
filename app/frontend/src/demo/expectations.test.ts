import { describe, expect, it } from 'vitest';
import { base } from './fixtures';
import { applyExpectedStep, evaluate } from './expectations';

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
        offers_sent: 1,
      })?.mismatches,
    ).toEqual([]);
    expect(
      final('declines-upgrade', 5, {
        flight: 'AC1252',
        seat: '14A',
        offer_status: 'declined',
        offers_sent: 1,
      })?.mismatches,
    ).toEqual([]);
    expect(
      final('seat-taken', 2, {
        flight: 'AC1240',
        seat: '15A',
        seat_reason: '14A unavailable; assigned nearest open window seat 15A',
        journey_status: 'RESOLVED',
      })?.mismatches,
    ).toEqual([]);
    expect(
      final('hotel-unavailable', 1, { needs_human: true, journey_status: 'RESOLVING' })?.mismatches,
    ).toEqual([]);
    expect(
      final('upgrade-race', 6, {
        offer_status: 'invalidated',
        offers_sent: 1,
        charged: '0.00',
        seat: '14A',
      })?.mismatches,
    ).toEqual([]);
    expect(
      final('upgrade-race', 6, { offer_status: 'accepted', charged: '135.00', seat: '2A' })
        ?.mismatches.length,
    ).toBeGreaterThan(0);
  });

  it('flags offers_sent and seat_reason mismatches across presets (#403)', () => {
    expect(
      evaluate({
        ...base,
        scenario_id: 'happy-path',
        step_index: 1,
        journey_status: 'RESOLVED',
        flight: 'AC1240',
        seat: '14A',
        offers_sent: 1,
      })?.mismatches,
    ).toEqual(['offers_sent: expected 0, saw 1']);

    expect(
      evaluate({
        ...base,
        scenario_id: 'unsatisfiable-request',
        step_index: 3,
        journey_status: 'RESOLVED',
        flight: 'AC1240',
        offer_status: 'sent',
        offers_sent: 1,
      })?.mismatches,
    ).toEqual(['offer: expected none, saw sent', 'offers_sent: expected 0, saw 1']);

    expect(
      evaluate({
        ...base,
        scenario_id: 'hotel-unavailable',
        step_index: 1,
        journey_status: 'RESOLVING',
        needs_human: true,
        offer_status: 'sent',
        offers_sent: 1,
      })?.mismatches,
    ).toEqual(['offer: expected none, saw sent', 'offers_sent: expected 0, saw 1']);

    expect(
      evaluate({
        ...base,
        scenario_id: 'seat-taken',
        step_index: 1,
        journey_status: 'RESOLVED',
        flight: 'AC1240',
        seat: '15A',
        seat_reason: null,
      })?.mismatches,
    ).toEqual(['seat_reason: expected reason recorded, saw none']);
  });

  it('returns null for unknown presets', () => {
    expect(evaluate({ ...base, scenario_id: 'nope', step_index: 1 })).toBeNull();
  });

  it('applyExpectedStep merges expected outcome fields and clears last_message across all preset steps', () => {
    const cases: Array<[string, number]> = [
      ['happy-path', 5],
      ['declines-upgrade', 5],
      ['unsatisfiable-request', 3],
      ['seat-taken', 2],
      ['hotel-unavailable', 2],
      ['upgrade-race', 6],
    ];
    for (const [scenarioId, totalSteps] of cases) {
      let cur = {
        ...base,
        scenario_id: scenarioId,
        total_steps: totalSteps,
      };
      for (let i = 1; i <= totalSteps; i += 1) {
        cur = applyExpectedStep({ ...cur, last_message: 'stale message' });
        expect(cur.step_index).toBe(i);
        expect(cur.done).toBe(i === totalSteps);
        expect(cur.last_message).toBeNull();
        expect(evaluate(cur)?.mismatches).toEqual([]);
      }
    }
  });
});
