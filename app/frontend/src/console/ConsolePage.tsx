import { useCallback, useEffect, useRef, useState } from 'react';
import { eventsUrl, getJourney, resetScenario, triggerCancellation } from './api';
import './console.css';
import { JourneyCard } from './JourneyCard';
import { Timeline } from './Timeline';
import type { JourneyDetail, JourneyEvent } from './types';

export function ConsolePage() {
  const [journey, setJourney] = useState<JourneyDetail | null>(null);
  const [events, setEvents] = useState<JourneyEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const source = useRef<EventSource | null>(null);

  const close = useCallback(() => {
    source.current?.close();
    source.current = null;
  }, []);
  useEffect(() => close, [close]);

  const open = useCallback(
    (id: string) => {
      close();
      const es = new EventSource(eventsUrl(id));
      source.current = es;
      es.onmessage = (m: MessageEvent<string>) => {
        const event = JSON.parse(m.data) as JourneyEvent;
        setEvents((prev) => [...prev, event]);
        getJourney(id)
          .then((j) => source.current === es && setJourney(j))
          .catch(() => setError('Could not refresh the journey. Events are still streaming.'));
      };
    },
    [close],
  );

  async function trigger() {
    setBusy(true);
    setError(null);
    try {
      const [id] = await triggerCancellation('AC1234', 'unscheduled maintenance');
      if (!id) throw new Error('no journey');
      setEvents([]);
      setJourney(await getJourney(id));
      open(id);
    } catch {
      setError(
        'Could not trigger the cancellation. Check that the API is running, then try again.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    try {
      await resetScenario();
      close();
      setJourney(null);
      setEvents([]);
    } catch {
      setError('Could not reset the scenario. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="console">
      <div className="console-head">
        <h1>Operations console</h1>
        <div className="actions">
          <button type="button" className="primary" onClick={trigger} disabled={busy}>
            Trigger AC1234 cancellation
          </button>
          <button type="button" onClick={reset} disabled={busy}>
            Reset
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="console-grid">
        <JourneyCard journey={journey} />
        <Timeline events={events} />
      </div>
    </section>
  );
}
