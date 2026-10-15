import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DemoPanel } from './DemoPanel';
import { base, presets } from './fixtures';

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

describe('DemoPanel', () => {
  const onStarted = vi.fn();
  const onChanged = vi.fn();
  let fetchMock: ReturnType<typeof vi.fn>;
  let state = base;

  beforeEach(() => {
    onStarted.mockClear();
    onChanged.mockClear();
    state = base;
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === '/api/scenarios') return json(presets);
      if (url.endsWith('/start')) return json(state);
      if (url.endsWith('/step')) {
        state = {
          ...base,
          step_index: 1,
          last_step: 'OCC cancels AC1234',
          next_step: 'Time passes',
          journey_id: 'J-1',
          journey_status: 'RESOLVED',
          flight: 'AC1240',
          seat: '14A',
        };
        return json(state);
      }
      if (url.includes('/whatsapp/')) return json({ ok: init?.body });
      return json({}, 404);
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  it('labels itself as a mocked demo', async () => {
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    expect(await screen.findByText(/demo, all systems mocked/i)).toBeInTheDocument();
  });

  it('lists the presets with their description', async () => {
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    const picker = await screen.findByRole('combobox', { name: 'Scenario' });
    expect(picker).toHaveDisplayValue('Happy path');
    expect(screen.getByText('Scenarios 1-5 end to end.')).toBeInTheDocument();
    await userEvent.selectOptions(picker, 'seat-taken');
    expect(screen.getByText('14A is gone on AC1240.')).toBeInTheDocument();
  });

  it('keeps Next step disabled until the scenario is started', async () => {
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    expect(await screen.findByRole('button', { name: 'Next step' })).toBeDisabled();
  });

  it('starts, steps and shows expected vs observed with a pass result', async () => {
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Start scenario' }));
    expect(fetchMock).toHaveBeenCalledWith('/api/scenarios/happy-path/start', { method: 'POST' });
    expect(onStarted).toHaveBeenCalled();
    expect(screen.getByText(/next: occ cancels ac1234/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(await screen.findByText(/Rebooked on AC1240 with seat 14A/)).toBeInTheDocument();
    expect(screen.getByText('Matches the expectation')).toBeInTheDocument();
    expect(screen.getByText('Step 1 of 3')).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ journey_id: 'J-1' }));
  });

  it('flags a mismatch in words', async () => {
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Start scenario' }));
    fetchMock.mockImplementationOnce(() =>
      json({ ...base, step_index: 1, journey_status: 'DETECTED', flight: 'AC1240', seat: '14A' }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(await screen.findByText('Differs from the expectation')).toBeInTheDocument();
    expect(screen.getByText('status: expected RESOLVED, saw DETECTED')).toBeInTheDocument();
  });

  it('disables Next step when the script is done', async () => {
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Start scenario' }));
    fetchMock.mockImplementationOnce(() =>
      json({ ...base, step_index: 3, done: true, next_step: null }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next step' })).toBeDisabled());
    expect(screen.getByText(/scenario finished/i)).toBeInTheDocument();
  });

  it('sends quick replies and offer taps through the WhatsApp API', async () => {
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: 'after 1pm' }));
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/whatsapp/P-MARIANA/messages',
      expect.objectContaining({ body: JSON.stringify({ text: 'Can I leave after 1pm?' }) }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Accept' }));
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/whatsapp/P-MARIANA/actions',
      expect.objectContaining({ body: JSON.stringify({ action: 'accept_offer' }) }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'No thanks' }));
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/whatsapp/P-MARIANA/actions',
      expect.objectContaining({ body: JSON.stringify({ action: 'decline_offer' }) }),
    );
    expect(onChanged).toHaveBeenCalledTimes(3);
  });

  it('shows an actionable alert when the scenarios cannot load', async () => {
    fetchMock.mockImplementationOnce(() => json({}, 404));
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/scenarios are unavailable/i);
  });

  it('clears active scenario state and error when resetToken increments', async () => {
    const { rerender } = render(
      <DemoPanel
        passengerId="P-MARIANA"
        onStarted={onStarted}
        onChanged={onChanged}
        resetToken={0}
      />,
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Start scenario' }));
    expect(screen.getByRole('button', { name: 'Next step' })).toBeEnabled();

    rerender(
      <DemoPanel
        passengerId="P-MARIANA"
        onStarted={onStarted}
        onChanged={onChanged}
        resetToken={1}
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next step' })).toBeDisabled());
  });

  it('surfaces 429 Retry-After busy feedback and refreshes on 503 StepApplied', async () => {
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Start scenario' }));

    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            detail: 'The Agent Engine query quota is exhausted (step); retry shortly.',
          }),
          { status: 429, headers: { 'Retry-After': '20' } },
        ),
      ),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Engine busy (429): The Agent Engine query quota is exhausted (step); retry shortly. Retry in 20s.',
    );
    expect(onChanged).not.toHaveBeenCalled();

    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            detail: 'The step was applied but its state could not be read: refresh.',
          }),
          { status: 503, headers: { 'Retry-After': '5' } },
        ),
      ),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Engine busy (503): The step was applied but its state could not be read: refresh. Retry in 5s.',
    );
    expect(onChanged).toHaveBeenCalledTimes(1);
  });
});
