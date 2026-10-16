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

export interface EngineTrace {
  trace_id: string;
  name: string;
  start_time: string;
  duration_ms: number;
  status: 'ok' | 'error';
  console_url: string;
}
export interface AgentEngine {
  name: string;
  engine_id: string;
  requests: number;
  errors: number;
  error_rate: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  trace_access: 'granted' | 'not_granted';
  traces: EngineTrace[] | null;
}
export interface AgentEngineResponse extends Envelope {
  hours: number;
  engines: AgentEngine[];
}

export interface CollectorHealthResponse extends Envelope {
  healthy: boolean;
  snapshot_age_seconds: number | null;
  last_execution: { ts: string; status: string; detail: string | null } | null;
}

export interface IrropsResponse extends Envelope {
  hours: number;
  guardrail: { rule: string; outcome: 'approve' | 'reject'; count: number }[];
  offers_proposed: number | null;
  offers_accepted: number | null;
  offers_declined: number | null;
  template_fallbacks: number | null;
  template_fallback_rate: number | null;
  ack_vetoes?: number | null;
  needs_human: number | null;
  notification_count: number | null;
  notification_p50_s: number | null;
  notification_p95_s: number | null;
  errors_5xx: number | null;
  errors_5xx_per_hour: number | null;
  engine_rebuilds: number | null;
  engine_latency_p50_ms: number | null;
  engine_latency_p95_ms: number | null;
  demo_health: 'healthy' | 'degraded' | 'down' | 'unknown';
  last_metric_at: string | null;
}

export interface Endpoints {
  'collector-health': CollectorHealthResponse;
  spend: SpendResponse;
  models: ModelsResponse;
  agents: AgentsResponse;
  github: GithubResponse;
  budget: BudgetResponse;
  'agent-engine': AgentEngineResponse;
  irrops: IrropsResponse;
}

export async function fetchApi<K extends keyof Endpoints>(
  name: K,
  signal?: AbortSignal,
  query = '',
): Promise<Endpoints[K]> {
  const res = await fetch(`/api/${name}${query}`, { signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Endpoints[K];
}
