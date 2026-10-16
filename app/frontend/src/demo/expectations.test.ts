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
        journey_status: 'CLOSED',
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
        seat_reason:
          '14A unavailable on AC1240; assigned nearest window seat 15A as a diamond tier courtesy',
        journey_status: 'RESOLVED',
      })?.mismatches,
    ).toEqual([]);
    expect(
      final('hotel-unavailable', 1, {
        needs_human: true,
        journey_status: 'RESOLVING',
        flight: 'AC1240',
        seat: '14A',
      })?.mismatches,
    ).toEqual([]);
    expect(
      final('upgrade-race', 6, {
        journey_status: 'RESOLVED',
        flight: 'AC1252',
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
        flight: 'AC1240',
        seat: '14A',
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

  it('matches ScenarioRunner.state() on upgrade-race steps 5-6, declines-upgrade step 5, hotel-unavailable steps 1-2, and seat-taken seat_reason (#443)', () => {
    // upgrade-race steps 5 and 6 require status RESOLVED, flight AC1252, seat 14A, offer invalidated, offersSent 1, charged 0.00
    for (const stepIndex of [5, 6]) {
      expect(
        evaluate({
          ...base,
          scenario_id: 'upgrade-race',
          step_index: stepIndex,
          journey_status: 'REPLANNING',
          flight: 'AC1240',
          seat: '14A',
          offer_status: 'invalidated',
          offers_sent: 1,
          charged: '0.00',
        })?.mismatches,
      ).toEqual([
        'status: expected RESOLVED, saw REPLANNING',
        'flight: expected AC1252, saw AC1240',
      ]);
    }
    const raceStep5 = applyExpectedStep({
      ...base,
      scenario_id: 'upgrade-race',
      total_steps: 6,
      step_index: 4,
    });
    expect(raceStep5.journey_status).toBe('RESOLVED');
    expect(raceStep5.flight).toBe('AC1252');
    expect(raceStep5.seat).toBe('14A');
    expect(raceStep5.offer_status).toBe('invalidated');
    expect(raceStep5.offers_sent).toBe(1);
    expect(raceStep5.charged).toBe('0.00');

    // declines-upgrade step 5 requires status CLOSED
    expect(
      evaluate({
        ...base,
        scenario_id: 'declines-upgrade',
        step_index: 5,
        journey_status: 'RESOLVED',
        flight: 'AC1252',
        seat: '14A',
        offer_status: 'declined',
        offers_sent: 1,
        charged: '0.00',
      })?.mismatches,
    ).toEqual(['status: expected CLOSED, saw RESOLVED']);
    const declinedStep5 = applyExpectedStep({
      ...base,
      scenario_id: 'declines-upgrade',
      total_steps: 5,
      step_index: 4,
      journey_status: 'RESOLVED',
    });
    expect(declinedStep5.journey_status).toBe('CLOSED');

    // hotel-unavailable steps 1 and 2 require status RESOLVING, flight AC1240, seat 14A
    for (const stepIndex of [1, 2]) {
      const hu = applyExpectedStep({
        ...base,
        scenario_id: 'hotel-unavailable',
        total_steps: 2,
        step_index: stepIndex - 1,
      });
      expect(hu.journey_status).toBe('RESOLVING');
      expect(hu.flight).toBe('AC1240');
      expect(hu.seat).toBe('14A');
      expect(hu.needs_human).toBe(true);
      expect(hu.offer_status).toBeNull();
      expect(hu.offers_sent).toBe(0);
      expect(hu.charged).toBe('0.00');
    }

    // seat-taken applyExpectedStep populates realistic SeatSpecialist reason when prev.seat_reason is null
    const seatTakenStep1 = applyExpectedStep({
      ...base,
      scenario_id: 'seat-taken',
      total_steps: 2,
      step_index: 0,
      seat_reason: null,
    });
    expect(seatTakenStep1.seat_reason).toBe(
      '14A unavailable on AC1240; assigned nearest window seat 15A as a diamond tier courtesy',
    );
  });
});
