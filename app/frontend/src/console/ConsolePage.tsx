import { useCallback, useEffect, useRef, useState } from 'react';
import { eventsUrl, getJourney, listJourneys, resetScenario, triggerCancellation } from './api';
import './console.css';
import { DemoPanel } from '../demo/DemoPanel';
import type { ScenarioState } from '../demo/types';
import { ChatView } from '../whatsapp/ChatView';
import { JourneyCard } from './JourneyCard';
import { Timeline } from './Timeline';
import type { JourneyDetail, JourneyEvent } from './types';

// Mariana's passenger id in the seeded AC1234 scenario.
const PASSENGER_ID = 'P-MARIANA';

export function ConsolePage() {
  const [journey, setJourney] = useState<JourneyDetail | null>(null);
  const [events, setEvents] = useState<JourneyEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resetSeq, setResetSeq] = useState(0);
  const [chatSeq, setChatSeq] = useState(0);
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
      const seenIds = new Set<string>();
      let inflight = false;
      let dirty = false;

      const refreshJourney = async () => {
        if (inflight) {
          dirty = true;
          return;
        }
        inflight = true;
        try {
          do {
            dirty = false;
            const j = await getJourney(id);
            if (source.current !== es) return;
            setJourney(j);
          } while (dirty && source.current === es);
        } catch {
          if (source.current === es) {
            setError('Could not refresh the journey. Events are still streaming.');
          }
        } finally {
          inflight = false;
        }
      };

      es.onmessage = (m: MessageEvent<string>) => {
        if (m.lastEventId) {
          if (seenIds.has(m.lastEventId)) return;
          seenIds.add(m.lastEventId);
        }
        const event = JSON.parse(m.data) as JourneyEvent;
        setEvents((prev) => [...prev, event]);
        void refreshJourney();
      };
    },
    [close],
  );

  const attach = useCallback(
    async (id: string) => {
      setEvents([]); // the stream replays the whole timeline
      setJourney(await getJourney(id));
      open(id);
    },
    [open],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const all = await listJourneys();
        const current = all.find((j) => j.status !== 'CLOSED') ?? all[0];
        if (current && !cancelled) await attach(current.journey_id);
      } catch {
        if (!cancelled) setError('Could not load the current journey. Reload the page to retry.');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attach]);

  const currentId = journey?.journey_id ?? null;

  function demoStarted() {
    close();
    setJourney(null);
    setEvents([]);
    setError(null);
    setChatSeq((n) => n + 1);
  }

  async function demoChanged(state?: ScenarioState) {
    const id = state?.journey_id ?? currentId;
    if (!id) return;
    try {
      if (source.current && currentId === id) setJourney(await getJourney(id));
      else await attach(id);
    } catch {
      setError('Could not refresh the journey.');
    }
  }

  async function trigger() {
    setBusy(true);
    setError(null);
    try {
      const [created] = await triggerCancellation('AC1234', 'unscheduled maintenance');
      // The backend ignores a duplicate cancellation; fall back to the journey it already has.
      const id = created ?? (await listJourneys())[0]?.journey_id;
      if (!id) throw new Error('no journey');
      await attach(id);
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
      setResetSeq((n) => n + 1);
      setChatSeq((n) => n + 1);
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
          <button
            type="button"
            className="primary"
            onClick={trigger}
            disabled={busy || journey !== null}
          >
            Trigger AC1234 cancellation
          </button>
          <button type="button" onClick={reset} disabled={busy}>
            Reset
          </button>
        </div>
      </div>
      {journey && <p className="hint">A journey is already open. Reset to run it again.</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <DemoPanel
        passengerId={PASSENGER_ID}
        onStarted={demoStarted}
        onChanged={demoChanged}
        resetToken={resetSeq}
      />
      <div className="console-grid">
        <JourneyCard journey={journey} />
        <Timeline events={events} />
        <section className="panel chat-col" aria-labelledby="chat-h">
          <h2 id="chat-h">Passenger chat (simulated)</h2>
          <ChatView key={chatSeq} passengerId={PASSENGER_ID} onChanged={() => void demoChanged()} />
        </section>
      </div>
    </section>
  );
}
