import { describe, expect, it } from 'vitest';
import contract from '../../../backend/openapi.json';
import { events as fixtureEvents } from './fixtures';
import { cabinLabel, reasonLabel } from './labels';
import { EVENT_TYPES, type GuardrailDecisionPayload } from './types';

// Drift check against the committed OpenAPI contract (app/backend/openapi.json, #61).
const schemas = contract.components.schemas as Record<
  string,
  {
    required?: string[];
    properties?: Record<string, { type?: string; const?: string; enum?: string[]; $ref?: string }>;
  }
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

  it('validates every event in console/fixtures.ts against EVENT_TYPES and OpenAPI payload schemas (#442)', () => {
    const knownTypes = new Set<string>(EVENT_TYPES);
    for (const ev of fixtureEvents) {
      expect(knownTypes.has(ev.type), `unknown event type in fixtures.ts: ${ev.type}`).toBe(true);
      const eventRef = events.discriminator.mapping[ev.type];
      expect(eventRef).toBeDefined();
      const eventSchemaName = eventRef!.split('/').pop()!;
      const payloadRef = schemas[eventSchemaName]?.properties?.['payload']?.$ref;
      expect(payloadRef).toBeDefined();
      const payloadSchemaName = payloadRef!.split('/').pop()!;
      const payloadSchema = schemas[payloadSchemaName];
      expect(payloadSchema).toBeDefined();
      const payloadKeys = Object.keys(ev.payload ?? {}).sort();
      expect(payloadKeys).toEqual((payloadSchema?.required ?? []).slice().sort());
    }
  });

  it('maps every guardrail, suppression, service_closure, and seat courtesy reason code to a human label (#442)', async () => {
    const fsSpec = 'node:fs';
    const { readFileSync } = (await import(/* @vite-ignore */ fsSpec)) as {
      readFileSync: (path: string, encoding: string) => string;
    };
    const guardrailPy = readFileSync('../backend/src/acme/policy/guardrail_offer.py', 'utf8');
    const skillsPy = readFileSync('../backend/src/acme/customer/skills.py', 'utf8');
    const eventsPy = readFileSync('../backend/src/acme/api/events.py', 'utf8');
    const approvalCodes = [...guardrailPy.matchAll(/rule\(\s*"([a-z_]+)"/g)].map((m) => m[1]!);
    const failureCodes = [
      ...guardrailPy.matchAll(/(?:\.append\(\s*"([a-z_]+)"\s*\)|\["([a-z_]+)"\])/g),
    ].map((m) => (m[1] ?? m[2])!);
    const ineligibleFn =
      skillsPy.split('def ineligible_reason')[1]?.split('class CustomerSkills')[0] ?? '';
    const suppressionCodes = [...ineligibleFn.matchAll(/return\s+"([a-z_]+)"/g)].map((m) => m[1]!);

    const policyCodes = new Set([...approvalCodes, ...failureCodes, ...suppressionCodes]);
    expect(new Set(approvalCodes).size).toBe(6);
    expect(new Set(failureCodes).size).toBe(13);
    expect(policyCodes.size).toBe(19);

    const serviceClosureReasonProp = schemas['ServiceClosurePayload']?.properties?.['reason'];
    const openApiClosureCodes = [
      ...(serviceClosureReasonProp?.const ? [serviceClosureReasonProp.const] : []),
      ...(serviceClosureReasonProp?.enum ?? []),
    ];
    const eventsPyClosureBlock =
      eventsPy.split('class ServiceClosurePayload')[1]?.split('class ')[0] ?? '';
    const pyClosureCodes = [...eventsPyClosureBlock.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]!);
    const closureCodes = new Set([...openApiClosureCodes, ...pyClosureCodes]);
    expect(closureCodes.has('offer_window_elapsed')).toBe(true);

    const seatCourtesyCodes = [
      'courtesy_upgrade_loyalty_tier',
      'ancillary_preserved',
      'equivalent_assigned',
      'unavailable_refund_queued',
    ];

    const allCodes = new Set([...policyCodes, ...closureCodes, ...seatCourtesyCodes]);
    for (const code of allCodes) {
      const label = reasonLabel(code);
      expect(label, `missing human-readable label for ${code}`).not.toBe(code);
      expect(label, `label for ${code} still looks like snake_case`).not.toContain('_');
    }

    const cabinEnum = schemas['SeatAssignment']?.properties?.['cabin']?.enum ?? [
      'economy',
      'premium_business',
    ];
    for (const cabin of cabinEnum) {
      expect(cabinLabel(cabin)).not.toContain('_');
    }
  });

  it('includes verification on JourneyDetail and attachments on WhatsAppMessage in OpenAPI', () => {
    expect(schemas['JourneyDetail']?.properties?.['verification']).toBeDefined();
    expect(schemas['WhatsAppMessage']?.properties?.['attachments']).toBeDefined();
  });
});
