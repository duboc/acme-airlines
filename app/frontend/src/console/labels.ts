export const STATUS_LABELS = {
  DETECTED: 'Detected',
  RESOLVING: 'Resolving',
  RESOLVED: 'Resolved',
  REPLANNING: 'Replanning',
  CLOSED: 'Closed',
} as const;

export const REASONS: Record<string, string> = {
  // 6 guardrail approval rule codes (acme/policy/guardrail_offer.py)
  disruption_resolved: 'Disruption is resolved',
  resolution_acknowledged: 'Passenger acknowledged the resolution',
  genuine_discount: 'Offer is a genuine courtesy discount',
  margin_verified: 'Positive net margin verified against inventory',
  zero_pressure: 'Optional offer with no prior offer on this journey',
  origin_and_flight_verified: 'Sales origin and current flight verified',
  // 12 guardrail rejection & sales suppression reason codes (guardrail_offer.py, customer/skills.py)
  disruption_not_resolved: 'Disruption is not resolved yet',
  resolution_not_acknowledged: 'Passenger has not acknowledged the resolution',
  open_request_pending: 'Passenger has an open request',
  price_claim_mismatch: 'Offer price or margin does not match inventory',
  no_inventory: 'No open seats in the offered cabin',
  not_a_genuine_discount: 'Offer is not a genuine courtesy discount',
  tier_not_eligible: 'Passenger tier is not eligible for recovery pricing',
  negative_margin: 'Net margin is negative',
  not_optional: 'Offer is not optional',
  offer_already_made: 'Another offer was already made for this journey',
  wrong_origin: 'Offer did not come from Sales',
  flight_mismatch: 'Offer is for a different flight',
  // Legacy aliases retained for backward compatibility with older fixtures
  not_acknowledged: 'Passenger has not acknowledged the resolution',
  open_request: 'Passenger has an open request',
  margin_negative: 'Net margin is negative',
};

export const reasonLabel = (code: string) => REASONS[code] ?? code;

export const eventLabel = (type: string) => {
  const text = type.replaceAll('_', ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
};
