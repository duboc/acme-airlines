import type { JourneyDetail, JourneyEvent } from './types';

export const detected: JourneyDetail = {
  journey_id: 'J-1',
  passenger_id: 'P-MARIANA',
  status: 'DETECTED',
  resolution_acknowledged: false,
  itinerary: [{ flight_number: 'AC1234' }],
  seat: { flight_number: 'AC1234', seat: '22C', cabin: 'economy' },
  bags: [{ tag: 'BG123', checked: true }],
  hotel: null,
  vouchers: [],
  offer: null,
};

export const resolved: JourneyDetail = {
  ...detected,
  status: 'RESOLVED',
  itinerary: [{ flight_number: 'AC1240' }],
  seat: { flight_number: 'AC1240', seat: '14A', cabin: 'economy' },
  hotel: { booking_id: 'HB-AD0362EF', hotel_id: 'HT8842', passenger_id: 'P-MARIANA', nights: 1 },
  vouchers: [
    { voucher_id: 'V1', passenger_id: 'P-MARIANA', kind: 'meal', amount: '40.00' },
    { voucher_id: 'V2', passenger_id: 'P-MARIANA', kind: 'transport', amount: '60.00' },
  ],
  offer: {
    offer_id: 'O1',
    flight: 'AC1240',
    price: '420.00',
    standard_price: '900.00',
    status: 'sent',
  },
};

export const events: JourneyEvent[] = [
  {
    actor: 'occ',
    type: 'cancellation_detected',
    timestamp: '2026-10-10T08:00:00Z',
    payload: { flight: 'AC1234', reason: 'unscheduled maintenance' },
  },
  {
    actor: 'sales',
    type: 'sales_suppressed',
    timestamp: '2026-10-10T08:00:05Z',
    payload: { reasons: ['disruption_not_resolved'] },
  },
  {
    actor: 'guardrail',
    type: 'offer_decision',
    timestamp: '2026-10-10T08:00:09Z',
    payload: { approved: false, reasons: ['not_acknowledged', 'margin_negative'] },
  },
  {
    actor: 'guardrail',
    type: 'offer_decision',
    timestamp: '2026-10-10T08:00:12Z',
    payload: { approved: true, reasons: ['disruption_resolved'] },
  },
  { actor: 'irrops', type: 'status_changed', timestamp: '2026-10-10T08:00:15Z', payload: {} },
];
