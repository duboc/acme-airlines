import { useCallback, useEffect, useRef, useState } from 'react';
import {
  eventsUrl,
  formatTransientError,
  getJourney,
  listJourneys,
  resetScenario,
  triggerCancellation,
} from './api';
import './console.css';
import { DemoPanel } from '../demo/DemoPanel';
import type { ScenarioState } from '../demo/types';
import { ChatView } from '../whatsapp/ChatView';
import { JourneyCard } from './JourneyCard';
import { Timeline } from './Timeline';
import type { JourneyDetail, JourneyEvent } from './types';

// Mariana's passenger id in the seeded AC1234 scenario.
const PASSENGER_ID = 'P-MARIANA';
const SSE_RECONNECT_MS = 5000;

export function ConsolePage() {
  const [journey, setJourney] = useState<JourneyDetail | null>(null);
  const [events, setEvents] = useState<JourneyEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState('unscheduled maintenance');
  const [resetSeq, setResetSeq] = useState(0);
  const [chatSeq, setChatSeq] = useState(0);
  const [chatRefresh, setChatRefresh] = useState(0);
  const source = useRef<EventSource | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const close = useCallback(() => {
    if (reconnectTimer.current !== null) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    source.current?.close();
    source.current = null;
  }, []);
  useEffect(() => close, [close]);

  const open = useCallback(
    function connect(id: string, seenIds: Set<string> = new Set<string>()) {
      close();
      const es = new EventSource(eventsUrl(id));
      source.current = es;
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
            setError(null);
          } while (dirty && source.current === es);
        } catch (err) {
          if (source.current === es) {
            setError(
              formatTransientError(
                err,
                'Could not refresh the journey. Events are still streaming.',
              ),
            );
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

      es.onerror = () => {
        if (source.current === es && (es as { readyState?: number }).readyState === 2) {
          reconnectTimer.current = setTimeout(() => {
            if (source.current === es) connect(id, seenIds);
          }, SSE_RECONNECT_MS);
        }
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
      } catch (err) {
        if (!cancelled) {
          setError(
            formatTransientError(
              err,
              'Could not load the current journey. Reload the page to retry.',
            ),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attach]);

  const currentId = journey?.journey_id ?? null;
  const activeFlight = journey?.itinerary[0]?.flight_number;
  const secondaryFlight = activeFlight && activeFlight !== 'AC1234' ? activeFlight : null;

  function demoStarted() {
    close();
    setJourney(null);
    setEvents([]);
    setError(null);
    setChatSeq((n) => n + 1);
  }

  async function demoChanged(state?: ScenarioState, refreshChat = true) {
    if (refreshChat) setChatRefresh((n) => n + 1);
    try {
      const id = state?.journey_id ?? currentId ?? (await listJourneys())[0]?.journey_id;
      if (!id) return;
      if (source.current && currentId === id) setJourney(await getJourney(id));
      else await attach(id);
      setError(null);
    } catch (err) {
      setError(formatTransientError(err, 'Could not refresh the journey.'));
    }
  }

  async function trigger(flight = 'AC1234') {
    setBusy(true);
    setError(null);
    try {
      const [created] = await triggerCancellation(flight, reason);
      // A manual cancellation ends the preset on the backend: clear the demo panel's progress.
      setResetSeq((n) => n + 1);
      // The backend ignores a duplicate cancellation; fall back to the journey it already has.
      const id = created ?? currentId ?? (await listJourneys())[0]?.journey_id;
      if (!id) throw new Error('no journey');
      await attach(id);
    } catch (err) {
      setError(
        formatTransientError(
          err,
          'Could not trigger the cancellation. Check that the API is running, then try again.',
        ),
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
    } catch (err) {
      setError(formatTransientError(err, 'Could not reset the scenario. Try again.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="console">
      <div className="console-head">
        <h1>Operations console</h1>
        <div className="actions">
          <select
            aria-label="Cancellation reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={busy}
          >
            <option value="unscheduled maintenance">Unscheduled maintenance (DOC-10)</option>
            <option value="severe weather">Force majeure: severe weather (DOC-11)</option>
          </select>
          <button
            type="button"
            className="primary"
            onClick={() => void trigger('AC1234')}
            disabled={busy || journey !== null}
          >
            Trigger AC1234 cancellation
          </button>
          {secondaryFlight && (
            <button type="button" onClick={() => void trigger(secondaryFlight)} disabled={busy}>
              Trigger {secondaryFlight} cancellation
            </button>
          )}
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
          <ChatView
            key={chatSeq}
            passengerId={PASSENGER_ID}
            offerStatus={journey?.offer?.status}
            refreshToken={chatRefresh}
            onChanged={() => void demoChanged(undefined, false)}
          />
        </section>
      </div>
    </section>
  );
}
