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
            type: 'offer_decision',
            timestamp: '2026-10-10T08:00:00Z',
            payload: { approved: false, reasons: ['brand_new_rule'] },
          },
        ]}
      />,
    );
    expect(screen.getByText('brand_new_rule')).toBeInTheDocument();
  });
});
