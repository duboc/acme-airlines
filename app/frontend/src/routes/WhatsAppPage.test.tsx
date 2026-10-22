import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WhatsAppPage } from './WhatsAppPage';

vi.mock('../whatsapp/ChatView', () => ({
  ChatView: ({ passengerId }: { passengerId: string }) => (
    <div data-testid="chat-view" data-passenger-id={passengerId}>
      Mocked ChatView
    </div>
  ),
}));

describe('WhatsAppPage', () => {
  it('renders heading, disclaimer, and ChatView with passengerId', () => {
    render(<WhatsAppPage />);

    expect(screen.getByRole('heading', { name: 'Passenger chat' })).toBeInTheDocument();
    expect(
      screen.getByText('This is a simulated WhatsApp conversation. No real messages are sent.'),
    ).toBeInTheDocument();

    const chatView = screen.getByTestId('chat-view');
    expect(chatView).toBeInTheDocument();
    expect(chatView).toHaveAttribute('data-passenger-id', 'P-MARIANA');
  });
});
