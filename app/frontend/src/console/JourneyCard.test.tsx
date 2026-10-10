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

  it('says what is not yet arranged', () => {
    render(<JourneyCard journey={detected} />);
    expect(screen.getAllByText('Not arranged yet').length).toBe(2);
  });

  it('shows acknowledgement state', () => {
    render(<JourneyCard journey={{ ...resolved, resolution_acknowledged: true }} />);
    expect(screen.getByText(/passenger acknowledged/i)).toBeInTheDocument();
  });
});
