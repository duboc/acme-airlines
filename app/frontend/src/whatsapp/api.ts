// Types mirror WhatsAppMessage / PassengerAction in app/backend/openapi.json.
export interface WhatsAppMessage {
  message_id: string;
  direction: 'inbound' | 'outbound';
  text: string;
  timestamp: string;
  buttons?: string[];
  attachments?: string[];
}

const ATTACHMENT_LABELS: Record<string, string> = {
  boarding_pass: 'Boarding pass',
  hotel_voucher: 'Hotel voucher',
  meal_voucher: 'Meal voucher',
};

export function attachmentLabel(kind: string): string {
  if (ATTACHMENT_LABELS[kind]) return ATTACHMENT_LABELS[kind];
  const text = kind.replaceAll('_', ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export type PassengerActionName = 'accept_offer' | 'decline_offer';

export const API_BASE = '/api';

export class ApiHttpError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    detail: string,
    retryAfterSeconds: number | null,
    fallbackMessage = `Request failed (${status})`,
  ) {
    super(detail || fallbackMessage);
    this.name = 'ApiHttpError';
    this.status = status;
    this.detail = detail;
    this.retryAfterSeconds = retryAfterSeconds;
  }

  get isTransient(): boolean {
    return this.status === 429 || this.status === 503;
  }
}

export async function throwApiError(res: Response, fallbackMessage?: string): Promise<never> {
  const retryHeader = res.headers?.get?.('Retry-After') ?? null;
  const parsedRetry = retryHeader !== null ? Number.parseInt(retryHeader, 10) : Number.NaN;
  const retryAfterSeconds = Number.isFinite(parsedRetry) && parsedRetry > 0 ? parsedRetry : null;
  let detail = '';
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body?.detail === 'string') detail = body.detail;
  } catch {
    // Non-JSON error response
  }
  throw new ApiHttpError(res.status, detail, retryAfterSeconds, fallbackMessage);
}

export function formatTransientError(err: unknown, fallback: string): string {
  if (err instanceof ApiHttpError && err.isTransient) {
    const base = err.detail || 'Engine is temporarily busy.';
    const wait = err.retryAfterSeconds ? ` Retry in ${err.retryAfterSeconds}s.` : '';
    return `Engine busy (${err.status}): ${base}${wait}`;
  }
  return fallback;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) return throwApiError(res, `Request failed (${res.status})`);
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const base = (id: string) => `${API_BASE}/whatsapp/${encodeURIComponent(id)}`;

export const fetchMessages = (id: string) => request<WhatsAppMessage[]>(`${base(id)}/messages`);
export const sendReply = (id: string, text: string) =>
  request<WhatsAppMessage>(`${base(id)}/messages`, json({ text }));
export const postAction = (id: string, action: PassengerActionName) =>
  request<{ offer_status: string }>(`${base(id)}/actions`, json({ action }));

/** The backend sends button labels only; map them to the action enum. */
export function actionForButton(label: string): PassengerActionName {
  return /decline|no thanks|not now|keep my/i.test(label) ? 'decline_offer' : 'accept_offer';
}
