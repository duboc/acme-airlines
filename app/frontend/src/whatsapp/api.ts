// Types mirror WhatsAppMessage / PassengerAction in app/backend/openapi.json.
export interface WhatsAppMessage {
  message_id: string;
  direction: 'inbound' | 'outbound';
  text: string;
  timestamp: string;
  buttons?: string[];
}

export type PassengerActionName = 'accept_offer' | 'decline_offer';

export const API_BASE = '/api';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
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
