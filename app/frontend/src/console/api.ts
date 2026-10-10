import type { JourneyDetail } from './types';

const BASE = '/api';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`${path} failed with ${res.status}`);
  return (await res.json()) as T;
}

export async function triggerCancellation(flight: string, reason: string): Promise<string[]> {
  const body = await request<{ journey_ids: string[] }>('/scenario/trigger-cancellation', {
    method: 'POST',
    body: JSON.stringify({ flight, reason }),
  });
  return body.journey_ids;
}

export async function resetScenario(): Promise<void> {
  await request('/scenario/reset', { method: 'POST' });
}

export function getJourney(id: string): Promise<JourneyDetail> {
  return request(`/journeys/${encodeURIComponent(id)}`);
}

export const eventsUrl = (id: string) => `${BASE}/journeys/${encodeURIComponent(id)}/events`;

export interface JourneySummary {
  journey_id: string;
  passenger_id: string;
  status: string;
}

export function listJourneys(): Promise<JourneySummary[]> {
  return request('/journeys');
}
