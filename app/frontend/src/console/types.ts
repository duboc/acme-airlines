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
}
export interface HotelBooking {
  booking_id: string;
  hotel_id: string;
  passenger_id: string;
  nights: number;
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
  vouchers: Voucher[];
  offer: OfferView | null;
}
export interface JourneyEvent {
  actor: string;
  type: string;
  timestamp: string;
  payload?: Record<string, unknown>;
}
