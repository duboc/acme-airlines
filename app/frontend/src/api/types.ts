// Hand-written stub. Replace with types generated from app/backend/openapi.json once #17 merges.

export const DISRUPTION_STATES = [
  'DETECTED',
  'RESOLVING',
  'RESOLVED',
  'REPLANNING',
  'CLOSED',
] as const;

export type DisruptionState = (typeof DISRUPTION_STATES)[number];

export function isDisruptionState(value: string): value is DisruptionState {
  return (DISRUPTION_STATES as readonly string[]).includes(value);
}
