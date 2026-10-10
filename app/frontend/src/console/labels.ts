export const STATUS_LABELS = {
  DETECTED: 'Detected',
  RESOLVING: 'Resolving',
  RESOLVED: 'Resolved',
  REPLANNING: 'Replanning',
  CLOSED: 'Closed',
} as const;

const REASONS: Record<string, string> = {
  disruption_not_resolved: 'Disruption is not resolved yet',
  disruption_resolved: 'Disruption is resolved',
  not_acknowledged: 'Passenger has not acknowledged the resolution',
  open_request: 'Passenger has an open request',
  margin_negative: 'Net margin is negative',
  not_optional: 'Offer is not optional',
  offer_already_made: 'Another offer was already made for this journey',
  wrong_origin: 'Offer did not come from Sales',
  flight_mismatch: 'Offer is for a different flight',
};

export const reasonLabel = (code: string) => REASONS[code] ?? code;

export const eventLabel = (type: string) => {
  const text = type.replaceAll('_', ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
};
