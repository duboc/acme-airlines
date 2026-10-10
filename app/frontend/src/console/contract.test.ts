import { describe, expect, it } from 'vitest';
import contract from '../../../backend/openapi.json';
import { EVENT_TYPES, type GuardrailDecisionPayload } from './types';

// Drift check against the committed OpenAPI contract (app/backend/openapi.json, #61).
const schemas = contract.components.schemas as Record<
  string,
  { required?: string[]; properties?: Record<string, { type?: string }> }
>;
const events = contract.paths['/journeys/{journey_id}/events'].get.responses['200'].content[
  'application/json'
].schema.items as { discriminator: { propertyName: string; mapping: Record<string, string> } };

describe('timeline event contract', () => {
  it('discriminates by type and lists exactly the event types the console knows', () => {
    expect(events.discriminator.propertyName).toBe('type');
    expect([...EVENT_TYPES].sort()).toEqual(Object.keys(events.discriminator.mapping).sort());
  });

  it('types the guardrail decision payload the timeline relies on', () => {
    const payload = schemas['GuardrailDecisionPayload'];
    expect(payload?.required?.sort()).toEqual(['approved', 'offer_id', 'reasons']);
    expect(payload?.properties?.['approved']?.type).toBe('boolean');
    const sample: GuardrailDecisionPayload = { approved: false, reasons: ['x'], offer_id: null };
    expect(Object.keys(sample).sort()).toEqual(payload?.required?.sort());
  });

  it('types the sales_suppressed payload', () => {
    expect(schemas['SalesSuppressedPayload']?.required?.sort()).toEqual([
      'journey_status',
      'reason',
    ]);
  });
});
