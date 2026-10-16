import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { events } from './fixtures';
import { Timeline } from './Timeline';

describe('Timeline', () => {
  it('shows an empty state', () => {
    render(<Timeline events={[]} />);
    expect(screen.getByText(/waiting for events/i)).toBeInTheDocument();
  });

  it('is a polite live log region', () => {
    render(<Timeline events={events} />);
    const log = screen.getByRole('log', { name: 'Agent timeline' });
    expect(log).toHaveAttribute('aria-live', 'polite');
    expect(screen.getAllByRole('listitem')).toHaveLength(events.length);
  });

  it('shows actor, event and time for each entry', () => {
    render(<Timeline events={events} />);
    const first = screen.getAllByRole('listitem')[0]!;
    expect(first).toHaveTextContent('occ');
    expect(first).toHaveTextContent('Cancellation detected');
    expect(first.querySelector('time')).toHaveAttribute('datetime', '2026-10-10T08:00:00Z');
  });

  it('shows a rejected guardrail decision with reasons in a distinct style', () => {
    render(<Timeline events={events} />);
    const rejected = screen.getAllByRole('listitem')[2]!;
    expect(rejected).toHaveAttribute('data-kind', 'guardrail-rejected');
    expect(rejected).toHaveTextContent('Rejected');
    expect(rejected).toHaveTextContent('Passenger has not acknowledged the resolution');
    expect(rejected).toHaveTextContent('Net margin is negative');
  });

  it('marks approved guardrail decisions and suppressed sales entries', () => {
    render(<Timeline events={events} />);
    const items = screen.getAllByRole('listitem');
    expect(items[3]).toHaveAttribute('data-kind', 'guardrail-approved');
    expect(items[3]).toHaveTextContent('Approved');
    expect(items[1]).toHaveAttribute('data-kind', 'sales-suppressed');
    expect(items[1]).toHaveTextContent('Sales held back');
    expect(items[1]).toHaveTextContent('Disruption is not resolved yet');
  });

  it('renders singular payload.reason on sales_suppressed events', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'customer_agent',
            type: 'sales_suppressed',
            timestamp: '2026-10-10T08:00:05Z',
            payload: { journey_status: 'RESOLVED', reason: 'resolution_not_acknowledged' },
          },
        ]}
      />,
    );
    const item = screen.getByRole('listitem');
    expect(item).toHaveAttribute('data-kind', 'sales-suppressed');
    expect(item).toHaveTextContent('Sales held back');
    expect(item.querySelector('.reasons')).toHaveTextContent(
      'Passenger has not acknowledged the resolution',
    );
  });

  it('falls back to the raw reason code when unknown', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'guardrail',
            type: 'guardrail_decision',
            timestamp: '2026-10-10T08:00:00Z',
            payload: { approved: false, reasons: ['brand_new_rule'], offer_id: null },
          },
        ]}
      />,
    );
    expect(screen.getByText('brand_new_rule')).toBeInTheDocument();
  });

  it('renders payload.reason across duty-of-care, seat_held, and lifecycle events (#413)', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'seat',
            type: 'seat_held',
            timestamp: '2026-10-10T08:01:00Z',
            payload: {
              flight: 'AC1240',
              seat: '15A',
              reason: '14A unavailable; assigned nearest window seat 15A',
            },
          },
          {
            actor: 'concierge_agent',
            type: 'hotel_booked',
            timestamp: '2026-10-10T08:02:00Z',
            payload: { hotel_id: 'HT8842', reason: 'ANAC 400 Art. 27, III' },
          },
          {
            actor: 'concierge_agent',
            type: 'voucher_issued',
            timestamp: '2026-10-10T08:03:00Z',
            payload: { kind: 'meal', amount: '25.00', reason: 'ANAC 400 Art. 27, II' },
          },
        ]}
      />,
    );
    expect(
      screen.getByText('14A unavailable; assigned nearest window seat 15A'),
    ).toBeInTheDocument();
    expect(screen.getByText('ANAC 400 Art. 27, III')).toBeInTheDocument();
    expect(screen.getByText('ANAC 400 Art. 27, II')).toBeInTheDocument();
  });

  it('renders needs_human step/error and duty_of_care_assessed rule_reasons (#413)', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'rights_agent',
            type: 'duty_of_care_assessed',
            timestamp: '2026-10-10T08:01:00Z',
            payload: {
              rule_reasons: {
                hotel: 'ANAC 400 Art. 27, III',
                meals: 'ANAC 400 Art. 27, II',
              },
            },
          },
          {
            actor: 'concierge_agent',
            type: 'needs_human',
            timestamp: '2026-10-10T08:02:00Z',
            payload: { step: 'hotel', error: 'hotel HT8842 has no rooms' },
          },
        ]}
      />,
    );
    expect(screen.getByText('hotel: ANAC 400 Art. 27, III')).toBeInTheDocument();
    expect(screen.getByText('meals: ANAC 400 Art. 27, II')).toBeInTheDocument();
    expect(screen.getByText('hotel: hotel HT8842 has no rooms')).toBeInTheDocument();
  });

  it('renders connection_constraint text and connection_verified margin summary (#413)', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'customer_agent',
            type: 'connection_constraint',
            timestamp: '2026-10-10T08:01:00Z',
            payload: {
              airport: 'SCL',
              mct_minutes: 90,
              text: 'must arrive SCL by 2026-10-10 12:30',
            },
          },
          {
            actor: 'customer_agent',
            type: 'connection_verified',
            timestamp: '2026-10-10T08:02:00Z',
            payload: {
              flight: 'AC1252',
              connection_flight: 'AC0800',
              feasible: true,
              margin_minutes: 10,
            },
          },
        ]}
      />,
    );
    expect(screen.getByText('must arrive SCL by 2026-10-10 12:30')).toBeInTheDocument();
    expect(screen.getByText('AC1252 → AC0800 (margin 10 min)')).toBeInTheDocument();
  });

  it('separates multiple reason spans with whitespace so log text does not concatenate adjacent reasons (#432)', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'rights_agent',
            type: 'duty_of_care_assessed',
            timestamp: '2026-10-10T08:01:00Z',
            payload: {
              rule_reasons: {
                hotel: 'ANAC 400 Art. 27, III',
                meals: 'ANAC 400 Art. 27, II',
              },
            },
          },
          {
            actor: 'guardrail',
            type: 'guardrail_decision',
            timestamp: '2026-10-10T08:02:00Z',
            payload: {
              approved: false,
              reasons: ['resolution_not_acknowledged', 'negative_margin'],
              offer_id: null,
            },
          },
        ]}
      />,
    );
    const items = screen.getAllByRole('listitem');
    expect(items[0]!.querySelector('.reasons')?.textContent).toBe(
      'hotel: ANAC 400 Art. 27, III meals: ANAC 400 Art. 27, II',
    );
    expect(items[1]!.querySelector('.reasons')?.textContent).toBe(
      'Passenger has not acknowledged the resolution Net margin is negative',
    );
  });

  it('maps all four seat courtesy reason codes to human-readable labels (#432)', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'seat',
            type: 'seat_held',
            timestamp: '2026-10-10T08:01:00Z',
            payload: { reason: 'courtesy_upgrade_loyalty_tier' },
          },
          {
            actor: 'seat',
            type: 'seat_held',
            timestamp: '2026-10-10T08:02:00Z',
            payload: { reason: 'ancillary_preserved' },
          },
          {
            actor: 'seat',
            type: 'seat_held',
            timestamp: '2026-10-10T08:03:00Z',
            payload: { reason: 'equivalent_assigned' },
          },
          {
            actor: 'seat',
            type: 'seat_held',
            timestamp: '2026-10-10T08:04:00Z',
            payload: { reason: 'unavailable_refund_queued' },
          },
        ]}
      />,
    );
    expect(screen.getByText('Courtesy upgrade for loyalty tier')).toBeInTheDocument();
    expect(screen.getByText('Paid seat selection preserved')).toBeInTheDocument();
    expect(screen.getByText('Equivalent seat assigned')).toBeInTheDocument();
    expect(screen.getByText('Preferred seat unavailable; refund queued')).toBeInTheDocument();
  });

  it('renders status_changed from → to, rebooked flight, and bag_rerouted tag → flight details (#432)', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'customer_agent',
            type: 'status_changed',
            timestamp: '2026-10-10T08:01:00Z',
            payload: { from: 'DETECTED', to: 'OUTREACH_SENT' },
          },
          {
            actor: 'customer_agent',
            type: 'rebooked',
            timestamp: '2026-10-10T08:02:00Z',
            payload: { flight: 'AC1252' },
          },
          {
            actor: 'customer_agent',
            type: 'bag_rerouted',
            timestamp: '2026-10-10T08:03:00Z',
            payload: { tag: 'BAG998877', flight: 'AC1252' },
          },
          {
            actor: 'customer_agent',
            type: 'bag_rerouted',
            timestamp: '2026-10-10T08:04:00Z',
            payload: { tag: 'BAG112233' },
          },
        ]}
      />,
    );
    expect(screen.getByText('DETECTED → OUTREACH_SENT')).toBeInTheDocument();
    expect(screen.getByText('AC1252')).toBeInTheDocument();
    expect(screen.getByText('BAG998877 → AC1252')).toBeInTheDocument();
    expect(screen.getByText('BAG112233')).toBeInTheDocument();
  });

  it('renders service_closure, upgrade_confirmed, seat_released, change_handoff, request_closed, resolution_blocked, and verification_failed payloads (#442)', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'customer_agent',
            type: 'service_closure',
            timestamp: '2026-10-10T08:01:00Z',
            payload: { reason: 'offer_window_elapsed' },
          },
          {
            actor: 'customer_agent',
            type: 'upgrade_confirmed',
            timestamp: '2026-10-10T08:02:00Z',
            payload: {
              flight: 'AC1252',
              seat: '2A',
              cabin: 'premium_business',
              price: '135.00',
              charge_id: 'CH-1',
            },
          },
          {
            actor: 'customer_agent',
            type: 'seat_released',
            timestamp: '2026-10-10T08:03:00Z',
            payload: { flight: 'AC1252', seat: '14A', replaced_by: '2A' },
          },
          {
            actor: 'customer_agent',
            type: 'change_handoff',
            timestamp: '2026-10-10T08:04:00Z',
            payload: { request_id: 'REQ-1', after: '10:00' },
          },
          {
            actor: 'customer_agent',
            type: 'request_closed',
            timestamp: '2026-10-10T08:05:00Z',
            payload: { request_id: 'REQ-1', outcome: 'satisfied' },
          },
          {
            actor: 'customer_agent',
            type: 'resolution_blocked',
            timestamp: '2026-10-10T08:06:00Z',
            payload: { missing: ['seat', 'hotel'] },
          },
          {
            actor: 'customer_agent',
            type: 'verification_failed',
            timestamp: '2026-10-10T08:07:00Z',
            payload: {
              failed: ['seat', 'hotel'],
              details: { seat: 'seat hold missing on AC1240', hotel: 'no booking found' },
              checks: { seat: false, hotel: false },
            },
          },
          {
            actor: 'customer_agent',
            type: 'verification_failed',
            timestamp: '2026-10-10T08:08:00Z',
            payload: {
              failed: ['baggage'],
              details: {},
              checks: { baggage: false },
            },
          },
        ]}
      />,
    );
    expect(screen.getByText('Offer window elapsed')).toBeInTheDocument();
    expect(screen.getByText('AC1252 · 2A ($135.00)')).toBeInTheDocument();
    expect(screen.getByText('14A → 2A')).toBeInTheDocument();
    expect(screen.getByText('after 10:00')).toBeInTheDocument();
    expect(screen.getByText('satisfied')).toBeInTheDocument();
    expect(screen.getByText('missing: seat, hotel')).toBeInTheDocument();
    expect(screen.getByText('seat: seat hold missing on AC1240')).toBeInTheDocument();
    expect(screen.getByText('hotel: no booking found')).toBeInTheDocument();
    expect(screen.getByText('baggage')).toBeInTheDocument();
  });

  it('surfaces Scenario 1-3 payload details: cancellation_detected, profile_loaded, seat_held, hotel_booked, transfer_arranged, and voucher_issued (#475)', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'occ',
            type: 'cancellation_detected',
            timestamp: '2026-10-10T08:00:00Z',
            payload: { flight: 'AC1234', reason: 'unscheduled maintenance' },
          },
          {
            actor: 'customer_agent',
            type: 'profile_loaded',
            timestamp: '2026-10-10T08:00:01Z',
            payload: {
              tier: 'diamond',
              seat: '14A',
              bags: 1,
              connection: { flight: 'AC2010', departure: '2026-10-10T14:00:00Z' },
            },
          },
          {
            actor: 'seat',
            type: 'seat_held',
            timestamp: '2026-10-10T08:00:02Z',
            payload: {
              flight: 'AC1240',
              seat: '14A',
              requested: '14A',
              reason: 'equivalent_assigned',
            },
          },
          {
            actor: 'seat',
            type: 'seat_held',
            timestamp: '2026-10-10T08:00:03Z',
            payload: {
              flight: 'AC1240',
              seat: '15A',
              requested: '14A',
              reason: '14A unavailable; assigned nearest window seat 15A',
            },
          },
          {
            actor: 'concierge_agent',
            type: 'hotel_booked',
            timestamp: '2026-10-10T08:00:04Z',
            payload: {
              booking_ref: 'HB-AD0362EF',
              hotel_id: 'HT8842',
              entitlement: 'DOC-05',
              reason: 'ANAC 400 Art. 27, III',
            },
          },
          {
            actor: 'concierge_agent',
            type: 'transfer_arranged',
            timestamp: '2026-10-10T08:00:05Z',
            payload: {
              transfer_ref: 'TR-1',
              leg: 'airport_to_hotel',
              entitlement: 'DOC-06',
              reason: 'ANAC 400 Art. 27, III',
            },
          },
          {
            actor: 'concierge_agent',
            type: 'transfer_arranged',
            timestamp: '2026-10-10T08:00:06Z',
            payload: {
              transfer_ref: 'TR-2',
              leg: 'hotel_to_airport',
              entitlement: 'DOC-06',
              reason: 'rescheduled for the new departure',
            },
          },
          {
            actor: 'concierge_agent',
            type: 'voucher_issued',
            timestamp: '2026-10-10T08:00:07Z',
            payload: {
              voucher_id: 'V-1',
              kind: 'meal',
              meal_type: 'dinner',
              amount: '25.00',
              entitlement: 'DOC-07',
              reason: 'ANAC 400 Art. 27, II',
            },
          },
          {
            actor: 'concierge_agent',
            type: 'voucher_issued',
            timestamp: '2026-10-10T08:00:08Z',
            payload: {
              voucher_id: 'V-2',
              kind: 'meal',
              meal_type: 'breakfast',
              amount: '12.00',
              entitlement: 'DOC-08',
              reason: 'ANAC 400 Art. 27, II',
            },
          },
        ]}
      />,
    );
    expect(screen.getByText('AC1234')).toBeInTheDocument();
    expect(screen.getByText('unscheduled maintenance')).toBeInTheDocument();
    expect(screen.getByText('diamond · seat 14A · 1 bag · connection AC2010')).toBeInTheDocument();
    expect(screen.getByText('AC1240 · 14A')).toBeInTheDocument();
    expect(screen.getByText('AC1240 · 15A (requested 14A)')).toBeInTheDocument();
    expect(screen.getByText('HT8842 (HB-AD0362EF)')).toBeInTheDocument();
    expect(screen.getByText('airport to hotel')).toBeInTheDocument();
    expect(screen.getByText('hotel to airport')).toBeInTheDocument();
    expect(screen.getByText('dinner ($25.00)')).toBeInTheDocument();
    expect(screen.getByText('breakfast ($12.00)')).toBeInTheDocument();
  });

  it('surfaces Scenario 4-5 commercial and closed-loop details: offer_proposed, analytics_recorded, memory_committed, crm_logged, and nps_scheduled (#475)', () => {
    render(
      <Timeline
        events={[
          {
            actor: 'sales',
            type: 'offer_proposed',
            timestamp: '2026-10-10T08:10:00Z',
            payload: { offer_id: 'O-1', flight: 'AC1252' },
          },
          {
            actor: 'customer_agent',
            type: 'analytics_recorded',
            timestamp: '2026-10-10T08:11:00Z',
            payload: {
              event: 'offer_accepted',
              converted: true,
              offer_id: 'O-1',
              price: '135.00',
              margin: '80.00',
            },
          },
          {
            actor: 'customer_agent',
            type: 'analytics_recorded',
            timestamp: '2026-10-10T08:12:00Z',
            payload: {
              event: 'offer_declined',
              converted: false,
              offer_id: 'O-1',
              price: null,
              margin: null,
            },
          },
          {
            actor: 'customer_agent',
            type: 'analytics_recorded',
            timestamp: '2026-10-10T08:13:00Z',
            payload: {
              event: 'no_offer',
              converted: false,
              offer_id: null,
              price: null,
              margin: null,
            },
          },
          {
            actor: 'customer_agent',
            type: 'memory_committed',
            timestamp: '2026-10-10T08:14:00Z',
            payload: { journey_id: 'J-MSLV42', events: 24 },
          },
          {
            actor: 'customer_agent',
            type: 'crm_logged',
            timestamp: '2026-10-10T08:15:00Z',
            payload: { interaction_id: 'INT-0001' },
          },
          {
            actor: 'customer_agent',
            type: 'nps_scheduled',
            timestamp: '2026-10-10T08:16:00Z',
            payload: { survey_id: 'NPS-0001', send_after: '2026-10-11T08:16:00Z' },
          },
        ]}
      />,
    );
    expect(screen.getByText('AC1252')).toBeInTheDocument();
    expect(screen.getByText('offer_accepted · $135.00 (+$80.00 margin)')).toBeInTheDocument();
    expect(screen.getByText('offer_declined')).toBeInTheDocument();
    expect(screen.getByText('no_offer')).toBeInTheDocument();
    expect(screen.getByText('24 events')).toBeInTheDocument();
    expect(screen.getByText('INT-0001')).toBeInTheDocument();
    expect(screen.getByText('NPS-0001')).toBeInTheDocument();
  });
});
