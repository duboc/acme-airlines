import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { actionForButton, fetchMessages, postAction, sendReply, type WhatsAppMessage } from './api';
import './chat.css';

const POLL_MS = 3000;

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  });
}

export function ChatView({
  passengerId,
  pollMs = POLL_MS,
}: {
  passengerId: string;
  pollMs?: number;
}) {
  const [messages, setMessages] = useState<WhatsAppMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  // Message ids whose buttons were already tapped: one action per offer.
  const [answered, setAnswered] = useState<Set<string>>(new Set());
  const endRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      setMessages(await fetchMessages(passengerId));
      setError('');
    } catch {
      setError('Cannot reach the messaging service. Retrying.');
    }
  }, [passengerId]);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const timer = setInterval(refresh, pollMs);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, pollMs]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [messages.length]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await sendReply(passengerId, text);
      setDraft('');
      await refresh();
    } catch {
      setError('Your message was not sent. Please try again.');
    } finally {
      setSending(false);
    }
  }

  async function onButton(msg: WhatsAppMessage, label: string) {
    if (answered.has(msg.message_id)) return;
    setAnswered((s) => new Set(s).add(msg.message_id));
    try {
      await postAction(passengerId, actionForButton(label));
      await refresh();
    } catch {
      setAnswered((s) => {
        const next = new Set(s);
        next.delete(msg.message_id);
        return next;
      });
      setError('Your choice was not sent. Please try again.');
    }
  }

  return (
    <div className="chat">
      <div className="chat-title">
        <strong>ACME Airlines</strong>
        <span>Simulated WhatsApp</span>
      </div>
      {error && (
        <p className="chat-error" role="alert">
          {error}
        </p>
      )}
      <div className="chat-log" role="log" aria-label="Conversation" aria-live="polite">
        {messages.length === 0 && <p className="chat-empty">No messages yet.</p>}
        {messages.map((m) => (
          <div key={m.message_id} className={`bubble-row ${m.direction}`}>
            <div className="bubble">
              <p className="bubble-text">{m.text}</p>
              <time dateTime={m.timestamp}>{formatTime(m.timestamp)} UTC</time>
              {m.buttons && m.buttons.length > 0 && (
                <div className="quick-replies" role="group" aria-label="Quick replies">
                  {m.buttons.map((b) => (
                    <button
                      key={b}
                      type="button"
                      disabled={answered.has(m.message_id)}
                      onClick={() => onButton(m, b)}
                    >
                      {b}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <form className="chat-input" onSubmit={onSubmit}>
        <label htmlFor="chat-draft" className="sr-only">
          Message
        </label>
        <input
          id="chat-draft"
          value={draft}
          maxLength={1000}
          autoComplete="off"
          placeholder="Type a message"
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" disabled={sending || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
