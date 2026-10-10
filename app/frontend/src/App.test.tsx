import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AppRoutes } from './App';

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

describe('app shell', () => {
  it('renders the console route', () => {
    renderAt('/console');
    expect(
      screen.getByRole('heading', { level: 1, name: 'Operations console' }),
    ).toBeInTheDocument();
  });

  it('renders the WhatsApp route and labels it simulated', () => {
    renderAt('/whatsapp');
    expect(screen.getByRole('heading', { level: 1, name: 'Passenger chat' })).toBeInTheDocument();
    expect(screen.getAllByText(/simulated/i).length).toBeGreaterThan(0);
  });

  it('redirects / to the console', () => {
    renderAt('/');
    expect(
      screen.getByRole('heading', { level: 1, name: 'Operations console' }),
    ).toBeInTheDocument();
  });

  it('marks the current route and navigates by keyboard-accessible links', async () => {
    renderAt('/console');
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(nav.querySelector('[aria-current="page"]')).toHaveTextContent('Console');
    await userEvent.click(screen.getByRole('link', { name: 'WhatsApp' }));
    expect(screen.getByRole('heading', { level: 1, name: 'Passenger chat' })).toBeInTheDocument();
  });

  it('offers a skip link to the main region', () => {
    renderAt('/console');
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main');
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main');
  });

  it('shows a not-found page for unknown routes', () => {
    renderAt('/nope');
    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument();
  });
});
