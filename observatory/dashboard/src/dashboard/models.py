"""Response models: the contract documented in API.md. Every response is an Envelope."""

from typing import Literal

from pydantic import BaseModel

State = Literal["ok", "empty", "lagging", "unavailable"]


class Envelope(BaseModel):
    state: State
    as_of: str | None = None  # ISO-8601 UTC ("...Z") of the newest data, null if none
    message: str | None = None  # human text for empty / lagging / unavailable


class SpendDay(BaseModel):
    date: str
    vm: float = 0.0
    cloud_run: float = 0.0
    vertex_ai: float = 0.0
    other: float = 0.0
    total: float = 0.0


class SpendResponse(Envelope):
    days: int
    currency: str = "USD"
    series: list[SpendDay] = []
    totals: SpendDay = SpendDay(date="total")


class ModelUsage(BaseModel):
    model: str  # opus | sonnet | haiku | gemini_flash | other
    label: str
    tokens: int
    requests: int


class ModelsResponse(Envelope):
    hours: int
    models: list[ModelUsage] = []


class AgentRow(BaseModel):
    name: str
    template: str | None = None
    role: str | None = None
    tier: str | None = None
    phase: str | None = None
    activity: str | None = None
    container_status: str | None = None
    started_at: str | None = None
    last_activity: str | None = None
    harness: str | None = None
    model: str | None = None
    limits_exceeded: bool = False
    running_seconds: int | None = None


class AgentsResponse(Envelope):
    agents: list[AgentRow] = []
    lead_last_activity: str | None = None
    limits_exceeded_count: int = 0


class Count(BaseModel):
    key: str
    count: int


class OpenPR(BaseModel):
    number: int
    title: str | None = None
    author: str | None = None
    created_at: str | None = None
    latest_verdict: str | None = None
    ci_conclusion: str | None = None


class GithubSnapshot(BaseModel):
    ts: str
    issues_by_status: list[Count]
    issues_by_label: list[Count]
    open_prs: list[OpenPR]
    merges_24h: int
    ci_conclusions: list[Count]
    ci_pass_rate: float | None = None  # success / total, null when there are no runs


class MergesDay(BaseModel):
    date: str
    count: int


class GithubResponse(Envelope):
    snapshot: GithubSnapshot | None = None
    merges_per_day: list[MergesDay] = []


class Marker(BaseModel):
    percent: int
    usd: float
    reached: bool


class GuardAction(BaseModel):
    ts: str
    cost_usd: float
    budget_usd: float
    action: str  # "no action" | "agents suspended"


class BudgetResponse(Envelope):
    budget_usd: float
    mtd_usd: float = 0.0
    percent: float = 0.0
    markers: list[Marker] = []
    guard_actions: list[GuardAction] = []


class EngineTrace(BaseModel):
    trace_id: str
    name: str  # root span name only; no attribute values are exposed
    start_time: str
    duration_ms: float
    status: str  # "ok" | "error"
    console_url: str


class EngineRow(BaseModel):
    name: str
    engine_id: str
    requests: int = 0
    errors: int = 0
    error_rate: float | None = None  # non-2xx / total, null when there were no requests
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    trace_access: str = "granted"  # "granted" | "not_granted"
    traces: list[EngineTrace] | None = None  # null when trace_access is "not_granted"


class AgentEngineResponse(Envelope):
    hours: int
    engines: list[EngineRow] = []


class CollectorRun(BaseModel):
    ts: str
    status: str  # "succeeded" | "failed"
    detail: str | None = None


class CollectorHealthResponse(Envelope):
    """as_of is the newest collector snapshot; healthy is false (red) when it is over 15 min old."""

    healthy: bool = False
    snapshot_age_seconds: int | None = None
    last_execution: CollectorRun | None = None


class RuleOutcome(BaseModel):
    rule: str  # guardrail reason code, or "all" for an approval
    outcome: str  # "approve" | "reject"
    count: int


class IrropsResponse(Envelope):
    """IRROPS product metrics (#235). Null means no series yet: never shown as zero."""

    hours: int
    guardrail: list[RuleOutcome] = []
    offers_proposed: int | None = None
    offers_accepted: int | None = None
    offers_declined: int | None = None
    template_fallbacks: int | None = None
    template_fallback_rate: float | None = (
        None  # fallbacks / notifications, null if no notifications
    )
    needs_human: int | None = None
    notification_count: int | None = None
    notification_p50_s: float | None = None
    notification_p95_s: float | None = None
    errors_5xx: int | None = None
    errors_5xx_per_hour: float | None = None
    engine_rebuilds: int | None = None
    engine_latency_p50_ms: float | None = None
    engine_latency_p95_ms: float | None = None
    demo_health: str = "unknown"  # "healthy" | "degraded" | "down" | "unknown"
    last_metric_at: str | None = None
