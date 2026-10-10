import { useEffect, useState } from 'react';
import { postAction, sendReply, type PassengerActionName } from '../whatsapp/api';
import { listScenarios, startScenario, stepScenario } from './api';
import './demo.css';
import { evaluate } from './expectations';
import type { PresetView, ScenarioState } from './types';

const QUICK_REPLIES = [
  { label: 'Can I leave after 10am?', text: 'Can I leave after 10am?' },
  { label: 'Perfect, thanks!', text: 'Perfect, thanks!' },
  { label: 'after 1pm', text: 'Can I leave after 1pm?' },
];
const TAPS: { label: string; action: PassengerActionName }[] = [
  { label: 'Accept', action: 'accept_offer' },
  { label: 'No thanks', action: 'decline_offer' },
];

interface Props {
  passengerId: string;
  /** The world was reset: drop any live view of the old journey. */
  onStarted: () => void;
  /** Something changed server-side; refresh the console (state is given for scenario steps). */
  onChanged: (state?: ScenarioState) => void;
}

export function DemoPanel({ passengerId, onStarted, onChanged }: Props) {
  const [presets, setPresets] = useState<PresetView[]>([]);
  const [selected, setSelected] = useState('');
  const [state, setState] = useState<ScenarioState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listScenarios()
      .then((p) => {
        setPresets(p);
        setSelected((cur) => cur || p[0]?.id || '');
      })
      .catch(() => setError('Scenarios are unavailable. They only run with the fake LLM mode.'));
  }, []);

  async function run(fn: () => Promise<void>, failure: string) {
    setBusy(true);
    setError('');
    try {
      await fn();
    } catch {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  const preset = presets.find((p) => p.id === selected);
  const active = state !== null && state.scenario_id === selected;
  const result = state ? evaluate(state) : null;

  return (
    <section className="demo" aria-labelledby="demo-h">
      <h2 id="demo-h">Guided scenario</h2>
      <p className="demo-badge">Demo, all systems mocked</p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="demo-pick">
        <label htmlFor="scenario">Scenario</label>
        <select id="scenario" value={selected} onChange={(e) => setSelected(e.target.value)}>
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={busy || !preset}
          onClick={() =>
            run(async () => {
              const s = await startScenario(selected);
              onStarted();
              setState(s);
            }, 'Could not start the scenario. Check that the API is running.')
          }
        >
          Start scenario
        </button>
      </div>
      {preset && (
        <>
          <p>{preset.description}</p>
          <p className="demo-final">
            <strong>Pass looks like:</strong> {preset.expectation}
          </p>
        </>
      )}
      <div className="demo-step">
        <button
          type="button"
          className="primary"
          disabled={busy || !active || state.done}
          onClick={() =>
            run(async () => {
              const s = await stepScenario(selected);
              setState(s);
              onChanged(s);
            }, 'Could not run the step. Start the scenario again.')
          }
        >
          Next step
        </button>
        {active && (
          <p>
            <span>
              Step {state.step_index} of {state.total_steps}
            </span>
            {state.done ? '. Scenario finished.' : `. Next: ${state.next_step}`}
          </p>
        )}
      </div>
      {active && result && (
        <div className="demo-expected" aria-live="polite">
          <h3>Expected now</h3>
          <p>{result.text}</p>
          {result.mismatches.length === 0 ? (
            <p className="pass">Matches the expectation</p>
          ) : (
            <>
              <p className="fail">Differs from the expectation</p>
              <ul>
                {result.mismatches.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </>
          )}
          {state.last_message && (
            <p className="demo-msg">Last message to passenger: {state.last_message}</p>
          )}
        </div>
      )}
      <fieldset className="demo-manual" disabled={busy}>
        <legend>Manual passenger actions</legend>
        {QUICK_REPLIES.map((q) => (
          <button
            type="button"
            key={q.label}
            onClick={() =>
              run(async () => {
                await sendReply(passengerId, q.text);
                onChanged();
              }, 'Could not send the reply.')
            }
          >
            {q.label}
          </button>
        ))}
        {TAPS.map((t) => (
          <button
            type="button"
            key={t.label}
            onClick={() =>
              run(async () => {
                await postAction(passengerId, t.action);
                onChanged();
              }, 'Could not send that tap. There may be no open offer.')
            }
          >
            {t.label}
          </button>
        ))}
      </fieldset>
    </section>
  );
}
