import { describe, expect, it } from 'vitest';
import contract from '../../../backend/openapi.json';
import { reasonLabel } from './labels';
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

  it('maps every guardrail and suppression reason code in backend policy source to a human label', async () => {
    const fsSpec = 'node:fs';
    const { readFileSync } = (await import(/* @vite-ignore */ fsSpec)) as {
      readFileSync: (path: string, encoding: string) => string;
    };
    const guardrailPy = readFileSync('../backend/src/acme/policy/guardrail_offer.py', 'utf8');
    const skillsPy = readFileSync('../backend/src/acme/customer/skills.py', 'utf8');
    const approvalCodes = [...guardrailPy.matchAll(/rule\(\s*"([a-z_]+)"/g)].map((m) => m[1]!);
    const failureCodes = [
      ...guardrailPy.matchAll(/(?:\.append\(\s*"([a-z_]+)"\s*\)|\["([a-z_]+)"\])/g),
    ].map((m) => (m[1] ?? m[2])!);
    const ineligibleFn =
      skillsPy.split('def ineligible_reason')[1]?.split('class CustomerSkills')[0] ?? '';
    const suppressionCodes = [...ineligibleFn.matchAll(/return\s+"([a-z_]+)"/g)].map((m) => m[1]!);

    const allCodes = new Set([...approvalCodes, ...failureCodes, ...suppressionCodes]);
    expect(new Set(approvalCodes).size).toBe(6);
    expect(new Set(failureCodes).size).toBe(13);
    expect(allCodes.size).toBe(19);

    for (const code of allCodes) {
      const label = reasonLabel(code);
      expect(label, `missing human-readable label for ${code}`).not.toBe(code);
      expect(label, `label for ${code} still looks like snake_case`).not.toContain('_');
    }
  });

  it('includes verification on JourneyDetail and attachments on WhatsAppMessage in OpenAPI', () => {
    expect(schemas['JourneyDetail']?.properties?.['verification']).toBeDefined();
    expect(schemas['WhatsAppMessage']?.properties?.['attachments']).toBeDefined();
  });
});
