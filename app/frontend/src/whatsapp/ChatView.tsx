import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import {
  ApiHttpError,
  actionForButton,
  attachmentLabel,
  fetchMessages,
  formatTransientError,
  postAction,
  sendReply,
  type WhatsAppMessage,
} from './api';
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
  offerStatus,
  refreshToken,
  onChanged,
}: {
  passengerId: string;
  pollMs?: number;
  offerStatus?: string | null;
  refreshToken?: number;
  onChanged?: () => void;
}) {
  const [messages, setMessages] = useState<WhatsAppMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  // Message ids whose buttons were already tapped: one action per offer.
  const [answered, setAnswered] = useState<
    Map<string, { chosen: string | null; invalidated?: boolean }>
  >(new Map());
  const cooldownUntil = useRef(0);
  const prevRefreshToken = useRef(refreshToken);
  const endRef = useRef<HTMLDivElement>(null);

  const noteCooldown = useCallback((err: unknown) => {
    if (err instanceof ApiHttpError && err.isTransient && err.retryAfterSeconds) {
      cooldownUntil.current = Date.now() + err.retryAfterSeconds * 1000;
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchMessages(passengerId);
      cooldownUntil.current = 0;
      setMessages(next);
      if (next.length === 0) {
        setAnswered((prev) => (prev.size === 0 ? prev : new Map()));
      }
      setError('');
    } catch (err) {
      noteCooldown(err);
      setError(formatTransientError(err, 'Cannot reach the messaging service. Retrying.'));
    }
  }, [passengerId, noteCooldown]);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const timer = setInterval(() => {
      if (Date.now() < cooldownUntil.current) return;
      void refresh();
    }, pollMs);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, pollMs]);

  useEffect(() => {
    if (prevRefreshToken.current === refreshToken) return;
    prevRefreshToken.current = refreshToken;
    if (Date.now() < cooldownUntil.current) return;
    void refresh();
  }, [refreshToken, refresh]);

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
      onChanged?.();
    } catch (err) {
      noteCooldown(err);
      setError(formatTransientError(err, 'Your message was not sent. Please try again.'));
    } finally {
      setSending(false);
    }
  }

  async function onButton(msg: WhatsAppMessage, label: string) {
    if (answered.has(msg.message_id)) return;
    setAnswered((s) => new Map(s).set(msg.message_id, { chosen: label }));
    try {
      const res = await postAction(passengerId, actionForButton(label));
      if (res?.offer_status === 'invalidated') {
        setAnswered((s) => new Map(s).set(msg.message_id, { chosen: null, invalidated: true }));
      }
      await refresh();
      onChanged?.();
    } catch (err) {
      noteCooldown(err);
      if (err instanceof ApiHttpError && err.status === 409) {
        setAnswered((s) => new Map(s).set(msg.message_id, { chosen: null }));
        await refresh();
        onChanged?.();
      } else {
        setAnswered((s) => {
          const next = new Map(s);
          next.delete(msg.message_id);
          return next;
        });
      }
      setError(formatTransientError(err, 'Your choice was not sent. Please try again.'));
    }
  }

  return (
    <div className="chat">
      <div className="chat-title">
        <strong>ACME Airlines</strong>
        <span>Simulated WhatsApp · times in UTC</span>
      </div>
      {error && (
        <div className="chat-error" role="alert">
          <span>{error}</span>
          <button type="button" onClick={refresh}>
            Retry
          </button>
        </div>
      )}
      <div className="chat-log" role="log" aria-label="Conversation">
        {messages.length === 0 && <p className="chat-empty">No messages yet.</p>}
        {messages.map((m, idx) => {
          const answer = answered.get(m.message_id);
          const isLatestOffer = !messages
            .slice(idx + 1)
            .some((later) => later.buttons && later.buttons.length > 0);
          const isInvalidated =
            Boolean(answer?.invalidated) || (isLatestOffer && offerStatus === 'invalidated');
          const statusSettled = offerStatus != null && offerStatus !== 'sent';
          const isSettled =
            answered.has(m.message_id) || idx < messages.length - 1 || statusSettled;
          const chosenLabel = isInvalidated ? null : answer?.chosen;
          const isChosen = (b: string) => {
            if (isInvalidated) return false;
            if (chosenLabel !== undefined && chosenLabel !== null) return chosenLabel === b;
            if (idx === messages.length - 1) {
              if (offerStatus === 'declined') return actionForButton(b) === 'decline_offer';
              if (offerStatus === 'accepted') return actionForButton(b) === 'accept_offer';
            }
            return false;
          };
          return (
            <div key={m.message_id} className={`bubble-row ${m.direction}`}>
              <div className="bubble">
                <p className="bubble-text">
                  <span className="sr-only">
                    {m.direction === 'inbound' ? 'You: ' : 'ACME Airlines: '}
                  </span>
                  {m.text}
                </p>
                {m.attachments && m.attachments.length > 0 && (
                  <div className="attachments" aria-label="Attachments">
                    {m.attachments.map((a) => (
                      <span key={a} className="attachment-chip">
                        {attachmentLabel(a)}
                      </span>
                    ))}
                  </div>
                )}
                <time dateTime={m.timestamp}>{formatTime(m.timestamp)}</time>
                {m.buttons && m.buttons.length > 0 && (
                  <>
                    <p className="quick-hint">
                      {isInvalidated
                        ? 'Offer no longer available.'
                        : 'Optional. You can ignore this.'}
                    </p>
                    <div className="quick-replies" role="group" aria-label="Quick replies">
                      {m.buttons.map((b) => (
                        <button
                          key={b}
                          type="button"
                          disabled={isSettled}
                          aria-pressed={isChosen(b)}
                          onClick={() => onButton(m, b)}
                        >
                          {isChosen(b) ? `✓ ${b}` : b}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          );
        })}
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
          {sending ? 'Sending…' : 'Send'}
        </button>
      </form>
    </div>
  );
}
