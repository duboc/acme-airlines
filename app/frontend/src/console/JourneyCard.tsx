import { isDisruptionState } from '../api/types';
import { STATUS_LABELS } from './labels';
import type { JourneyDetail } from './types';

const ORDER = Object.keys(STATUS_LABELS) as (keyof typeof STATUS_LABELS)[];
const NONE = 'Not arranged yet';

function Row({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="row">
      <dt>{term}</dt>
      <dd>{children ?? NONE}</dd>
    </div>
  );
}

export function JourneyCard({ journey }: { journey: JourneyDetail | null }) {
  if (!journey) {
    return (
      <section className="panel" aria-labelledby="journey-h">
        <h2 id="journey-h">Journey</h2>
        <p>No journey open. Trigger the cancellation to start one.</p>
      </section>
    );
  }
  const current = isDisruptionState(journey.status) ? journey.status : null;
  const { seat, hotel, offer, verification } = journey;
  const verificationChecks = verification ? Object.entries(verification) : [];
  return (
    <section className="panel" aria-labelledby="journey-h">
      <h2 id="journey-h">Journey {journey.journey_id}</h2>
      <ol className="stepper" aria-label="Disruption status">
        {ORDER.map((s) => (
          <li key={s} aria-current={s === current ? 'step' : undefined}>
            {STATUS_LABELS[s]}
          </li>
        ))}
      </ol>
      <p>
        {journey.resolution_acknowledged
          ? 'Passenger acknowledged the current resolution.'
          : 'Passenger has not acknowledged the resolution yet.'}
      </p>
      <dl className="itinerary">
        <Row term="Flight">{journey.itinerary.map((s) => s.flight_number).join(', ')}</Row>
        <Row term="Seat">
          {seat ? (
            <>
              <span>{seat.seat}</span> ({seat.cabin ?? 'economy'})
            </>
          ) : (
            NONE
          )}
        </Row>
        <Row term="Bag routing">{journey.bags.map((b) => b.tag).join(', ') || NONE}</Row>
        <Row term="Hotel">
          {hotel
            ? `${hotel.hotel_id} · Booking ${hotel.booking_id}, ${hotel.nights} night${hotel.nights > 1 ? 's' : ''}`
            : NONE}
        </Row>
        <Row term="Vouchers">
          {journey.vouchers.length
            ? journey.vouchers.map((v) => `${v.kind} ${v.amount}`).join(' · ')
            : NONE}
        </Row>
        <Row term="Upgrade offer">
          {offer ? `${offer.status}: ${offer.price} (standard ${offer.standard_price})` : 'None'}
        </Row>
        <Row term="Verification">
          {verificationChecks.length > 0
            ? verificationChecks.map(([k, ok]) => `${k}: ${ok ? '✓' : '✗'}`).join(' · ')
            : NONE}
        </Row>
      </dl>
    </section>
  );
}
