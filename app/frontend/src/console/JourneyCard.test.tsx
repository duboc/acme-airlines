import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { detected, resolved } from './fixtures';
import { JourneyCard } from './JourneyCard';

describe('JourneyCard', () => {
  it('shows an empty state without a journey', () => {
    render(<JourneyCard journey={null} />);
    expect(screen.getByText(/no journey open/i)).toBeInTheDocument();
  });

  it('shows the five states as a stepper with the current one marked', () => {
    render(<JourneyCard journey={resolved} />);
    const steps = screen.getByRole('list', { name: 'Disruption status' });
    const items = within(steps).getAllByRole('listitem');
    expect(items.map((i) => i.textContent)).toEqual([
      expect.stringContaining('Detected'),
      expect.stringContaining('Resolving'),
      expect.stringContaining('Resolved'),
      expect.stringContaining('Replanning'),
      expect.stringContaining('Closed'),
    ]);
    expect(within(steps).getByText('Resolved').closest('li')).toHaveAttribute(
      'aria-current',
      'step',
    );
  });

  it('shows the rebooked itinerary', () => {
    render(<JourneyCard journey={resolved} />);
    expect(screen.getByText('AC1240')).toBeInTheDocument();
    expect(screen.getByText('14A')).toBeInTheDocument();
    expect(screen.getByText('BG123')).toBeInTheDocument();
    expect(screen.getByText(/HT8842/)).toBeInTheDocument();
    expect(screen.getByText(/meal/i)).toBeInTheDocument();
    expect(screen.getByText(/420\.00/)).toBeInTheDocument();
  });

  it('leads the hotel row with the property code, booking reference second', () => {
    const journey = {
      ...resolved,
      hotel: { booking_id: 'HB-AD0362EF', hotel_id: 'HT8842', passenger_id: 'P', nights: 1 },
    };
    render(<JourneyCard journey={journey} />);
    expect(screen.getByText('HT8842 · Booking HB-AD0362EF, 1 night')).toBeInTheDocument();
  });

  it('says what is not yet arranged', () => {
    render(<JourneyCard journey={detected} />);
    expect(screen.getAllByText('Not arranged yet').length).toBe(3);
  });

  it('shows acknowledgement state', () => {
    render(<JourneyCard journey={{ ...resolved, resolution_acknowledged: true }} />);
    expect(screen.getByText(/passenger acknowledged/i)).toBeInTheDocument();
  });

  it('renders closed-loop verification checks when present and Not arranged yet when null', () => {
    const { rerender } = render(<JourneyCard journey={{ ...resolved, verification: null }} />);
    expect(screen.getByText('Verification').nextElementSibling).toHaveTextContent(
      'Not arranged yet',
    );

    rerender(
      <JourneyCard
        journey={{
          ...resolved,
          verification: {
            flight: true,
            seat: true,
            hotel: true,
            baggage: true,
            upgrade: false,
          },
        }}
      />,
    );
    expect(screen.getByText('Verification').nextElementSibling).toHaveTextContent(
      'flight: ✓ · seat: ✓ · hotel: ✓ · baggage: ✓ · upgrade: ✗',
    );
  });

  it('formats seat.cabin without snake_case for premium_business and economy (#442)', () => {
    const { rerender } = render(
      <JourneyCard
        journey={{
          ...resolved,
          seat: { flight_number: 'AC1252', seat: '2A', cabin: 'premium_business' },
        }}
      />,
    );
    expect(screen.getByText('Seat').nextElementSibling).toHaveTextContent('2A (premium business)');
    expect(screen.getByText('Seat').nextElementSibling?.textContent).not.toContain('_');

    rerender(
      <JourneyCard
        journey={{
          ...resolved,
          seat: { flight_number: 'AC1240', seat: '14A', cabin: 'economy' },
        }}
      />,
    );
    expect(screen.getByText('Seat').nextElementSibling).toHaveTextContent('14A (economy)');
  });
});
