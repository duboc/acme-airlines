import { API_BASE, ApiHttpError, formatTransientError, throwApiError } from '../whatsapp/api';
import type { PresetView, ScenarioState } from './types';

export { ApiHttpError, formatTransientError };

async function request<T>(path: string, method = 'GET'): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method });
  if (!res.ok) return throwApiError(res, `${path} failed with ${res.status}`);
  return (await res.json()) as T;
}

export const listScenarios = () => request<PresetView[]>('/scenarios');
export const startScenario = (id: string) =>
  request<ScenarioState>(`/scenarios/${encodeURIComponent(id)}/start`, 'POST');
export const stepScenario = (id: string) =>
  request<ScenarioState>(`/scenarios/${encodeURIComponent(id)}/step`, 'POST');
