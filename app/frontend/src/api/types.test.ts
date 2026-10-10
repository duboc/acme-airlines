import { describe, expect, it } from 'vitest';
import { DISRUPTION_STATES, isDisruptionState } from './types';

describe('api types (stub until #17)', () => {
  it('knows the disruption state machine from ADR 0100', () => {
    expect(DISRUPTION_STATES).toEqual([
      'DETECTED',
      'RESOLVING',
      'RESOLVED',
      'REPLANNING',
      'CLOSED',
    ]);
    expect(isDisruptionState('RESOLVED')).toBe(true);
    expect(isDisruptionState('nope')).toBe(false);
  });
});
