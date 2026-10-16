// Subset of app/backend/openapi.json used by the console. Keep in sync with the contract.
export type { DisruptionState } from '../api/types';

export interface Segment {
  flight_number: string;
}
export interface SeatAssignment {
  flight_number: string;
  seat: string;
  cabin?: 'economy' | 'premium_business';
}
export interface Bag {
  tag: string;
  checked?: boolean;
  routing?: string | null;
}
export interface HotelBooking {
  booking_id: string;
  hotel_id: string;
  passenger_id: string;
  nights: number;
}
export interface TransferView {
  transfer_ref: string;
  leg: string;
  origin: string;
  destination: string;
  pickup_at: string;
}
export interface Voucher {
  voucher_id: string;
  passenger_id: string;
  kind: 'meal' | 'hotel' | 'transport';
  amount: string;
}
export interface OfferView {
  offer_id: string;
  flight: string;
  price: string;
  standard_price: string;
  status: string;
}
export interface JourneyDetail {
  journey_id: string;
  passenger_id: string;
  status: string;
  resolution_acknowledged: boolean;
  itinerary: Segment[];
  seat: SeatAssignment | null;
  bags: Bag[];
  hotel: HotelBooking | null;
  transfers?: TransferView[];
  vouchers: Voucher[];
  offer: OfferView | null;
  verification?: Record<string, boolean> | null;
  needs_human?: boolean;
}
export interface JourneyEvent {
  actor: string;
  type: string;
  timestamp: string;
  payload?: Record<string, unknown>;
}

// Timeline event types in the OpenAPI contract (app/backend/openapi.json). contract.test.ts fails on drift.
export const EVENT_TYPES = [
  'accept_offer',
  'analytics_recorded',
  'bag_rerouted',
  'cancellation_detected',
  'change_handoff',
  'charge_voided',
  'connection_constraint',
  'connection_verified',
  'crm_logged',
  'decline_offer',
  'duty_of_care_assessed',
  'duty_of_care_fulfilled',
  'guardrail_decision',
  'hotel_booked',
  'memory_committed',
  'needs_human',
  'notification_sent',
  'nps_scheduled',
  'offer_proposed',
  'offer_sent',
  'passenger_message',
  'profile_loaded',
  'rebooked',
  'request_closed',
  'resolution_acknowledged',
  'resolution_blocked',
  'sales_opportunity',
  'sales_suppressed',
  'seat_held',
  'seat_released',
  'service_closure',
  'status_changed',
  'transfer_arranged',
  'upgrade_confirmed',
  'verification_failed',
  'verification_passed',
  'voucher_issued',
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export interface GuardrailDecisionPayload {
  approved: boolean;
  reasons: string[];
  offer_id: string | null;
}
export interface SalesSuppressedPayload {
  journey_status: string;
  reason: string;
}
