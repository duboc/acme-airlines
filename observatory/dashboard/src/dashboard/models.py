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
