// Typed client for the read-only dashboard API. Contract: observatory/dashboard/API.md.
export type DataState = 'ok' | 'empty' | 'lagging' | 'unavailable';

export interface Envelope {
  state: DataState;
  as_of: string | null;
  message: string | null;
}

export interface SpendRow {
  date: string;
  vm: number;
  cloud_run: number;
  vertex_ai: number;
  other: number;
  total: number;
}
export interface SpendResponse extends Envelope {
  days: number;
  currency: string;
  series: SpendRow[];
  totals: SpendRow;
}

export interface ModelRow {
  model: 'opus' | 'sonnet' | 'haiku' | 'gemini_flash' | 'other';
  label: string;
  tokens: number;
  requests: number;
}
export interface ModelsResponse extends Envelope {
  hours: number;
  models: ModelRow[];
}

export interface AgentRow {
  name: string;
  template: string;
  role: string;
  tier: string;
  phase: string;
  activity: string;
  container_status: string;
  started_at: string | null;
  last_activity: string | null;
  harness: string;
  model: string;
  limits_exceeded: boolean;
  running_seconds: number | null;
}
export interface AgentsResponse extends Envelope {
  lead_last_activity: string | null;
  limits_exceeded_count: number;
  agents: AgentRow[];
}

export interface Count {
  key: string;
  count: number;
}
export interface PullRequest {
  number: number;
  title: string;
  author: string;
  created_at: string;
  latest_verdict: 'APPROVE' | 'REQUEST CHANGES' | null;
  ci_conclusion: 'success' | 'failure' | 'pending' | null;
}
export interface GithubResponse extends Envelope {
  snapshot: {
    ts: string;
    issues_by_status: Count[];
    issues_by_label: Count[];
    open_prs: PullRequest[];
    merges_24h: number;
    ci_conclusions: Count[];
    ci_pass_rate: number | null;
  } | null;
  merges_per_day: { date: string; count: number }[];
}

export interface BudgetResponse extends Envelope {
  budget_usd: number;
  mtd_usd: number;
  percent: number;
  markers: { percent: number; usd: number; reached: boolean }[];
  guard_actions: { ts: string; cost_usd: number; budget_usd: number; action: string }[];
}

export interface Endpoints {
  spend: SpendResponse;
  models: ModelsResponse;
  agents: AgentsResponse;
  github: GithubResponse;
  budget: BudgetResponse;
}

export async function fetchApi<K extends keyof Endpoints>(
  name: K,
  signal?: AbortSignal,
): Promise<Endpoints[K]> {
  const res = await fetch(`/api/${name}`, { signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Endpoints[K];
}
