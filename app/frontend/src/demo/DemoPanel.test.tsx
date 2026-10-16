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

  it('flags a mismatch in words and renders seat_reason through reasonLabel when present', async () => {
    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Start scenario' }));
    fetchMock.mockImplementationOnce(() =>
      json({
        ...base,
        step_index: 1,
        journey_status: 'DETECTED',
        flight: 'AC1240',
        seat: '14A',
        seat_reason:
          '14A unavailable on AC1240; assigned nearest window seat 15A as a diamond tier courtesy',
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(await screen.findByText('Differs from the expectation')).toBeInTheDocument();
    expect(screen.getByText('status: expected RESOLVED, saw DETECTED')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Seat reason: 14A unavailable on AC1240; assigned nearest window seat 15A as a diamond tier courtesy',
      ),
    ).toBeInTheDocument();

    fetchMock.mockImplementationOnce(() =>
      json({
        ...base,
        step_index: 2,
        journey_status: 'RESOLVED',
        flight: 'AC1240',
        seat: '14A',
        seat_reason: 'courtesy_upgrade_loyalty_tier',
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(
      await screen.findByText('Seat reason: Courtesy upgrade for loyalty tier'),
    ).toBeInTheDocument();
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

  it('clears active scenario state and error when resetToken increments and does not resurrect it on manual action', async () => {
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
    expect(screen.getByText(/next: occ cancels ac1234/i)).toBeInTheDocument();

    rerender(
      <DemoPanel
        passengerId="P-MARIANA"
        onStarted={onStarted}
        onChanged={onChanged}
        resetToken={1}
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next step' })).toBeDisabled());
    expect(screen.queryByText(/next: occ cancels ac1234/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'after 1pm' }));
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/whatsapp/P-MARIANA/messages',
      expect.objectContaining({ body: JSON.stringify({ text: 'Can I leave after 1pm?' }) }),
    );
    expect(screen.getByRole('button', { name: 'Next step' })).toBeDisabled();
    expect(screen.queryByText(/next: occ cancels ac1234/i)).not.toBeInTheDocument();
  });

  it('surfaces 429 Retry-After busy feedback and advances step_index and done state on 503 StepApplied', async () => {
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

    // Step 1 returns 503 StepApplied -> advances to Step 1 of 3 (Next: Time passes) and calls onChanged()
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
    expect(screen.getByText('Step 1 of 3')).toBeInTheDocument();
    expect(screen.getByText(/next: time passes/i)).toBeInTheDocument();
    expect(screen.getByText('Matches the expectation')).toBeInTheDocument();
    expect(screen.queryByText('Differs from the expectation')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next step' })).toBeEnabled();

    // Advance normally to Step 2 of 3, then final Step 3 returns 503 StepApplied -> marks done and disables Next step
    fetchMock.mockImplementationOnce(() =>
      json({
        ...base,
        step_index: 2,
        last_step: 'Time passes',
        next_step: "Mariana: 'Can I leave after 10am?'",
        journey_status: 'RESOLVED',
        flight: 'AC1240',
        seat: '14A',
        last_message: 'We rebooked you on AC1240.',
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(await screen.findByText('Step 2 of 3')).toBeInTheDocument();
    expect(
      screen.getByText('Last message to passenger: We rebooked you on AC1240.'),
    ).toBeInTheDocument();

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
    expect(await screen.findByText('Step 3 of 3')).toBeInTheDocument();
    expect(screen.getByText(/scenario finished/i)).toBeInTheDocument();
    expect(screen.getByText('Matches the expectation')).toBeInTheDocument();
    expect(screen.queryByText('Differs from the expectation')).not.toBeInTheDocument();
    expect(screen.queryByText(/last message to passenger/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next step' })).toBeDisabled();
    expect(onChanged).toHaveBeenCalledTimes(3);
  });

  it('merges ScenarioState outcome fields on 503 StepApplied so evaluate(state) does not show false mismatches (#386)', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === '/api/scenarios')
        return json([
          {
            id: 'happy-path',
            title: 'Happy path',
            description: 'Scenarios 1-5 end to end.',
            steps: [
              'OCC cancels AC1234',
              'Time passes',
              "Mariana: 'Can I leave after 10am?'",
              "Mariana: 'Perfect, thanks!'",
              'Mariana taps Accept on the upgrade',
            ],
            expectation: 'Journey CLOSED on AC1252, seat 2A, $135.00 charged once.',
          },
        ]);
      if (url.endsWith('/start'))
        return json({
          ...base,
          total_steps: 5,
          step_index: 4,
          last_step: "Mariana: 'Perfect, thanks!'",
          next_step: 'Mariana taps Accept on the upgrade',
          journey_id: 'J-1',
          journey_status: 'RESOLVED',
          flight: 'AC1252',
          seat: '14A',
          offers_sent: 1,
          offer_status: 'sent',
          charged: '0.00',
          last_message: 'Upgrade offer sent.',
        });
      if (url.endsWith('/step'))
        return Promise.resolve(
          new Response(
            JSON.stringify({
              detail: 'The step was applied but its state could not be read: refresh.',
            }),
            { status: 503, headers: { 'Retry-After': '5' } },
          ),
        );
      return json({}, 404);
    });

    render(<DemoPanel passengerId="P-MARIANA" onStarted={onStarted} onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Start scenario' }));
    expect(screen.getByText('Last message to passenger: Upgrade offer sent.')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(await screen.findByText('Step 5 of 5')).toBeInTheDocument();
    expect(screen.getByText(/scenario finished/i)).toBeInTheDocument();
    expect(
      screen.getByText('Journey CLOSED on AC1252, seat 2A, $135.00 charged once.', {
        selector: '.demo-expected p',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText('Matches the expectation')).toBeInTheDocument();
    expect(screen.queryByText('Differs from the expectation')).not.toBeInTheDocument();
    expect(screen.queryByText(/last message to passenger/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next step' })).toBeDisabled();
  });
});
