"""Builds API responses from raw source records, deciding ok/empty/lagging/unavailable."""

import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from .models import (
    AgentRow,
    AgentsResponse,
    BudgetResponse,
    Count,
    GithubResponse,
    GithubSnapshot,
    GuardAction,
    Marker,
    MergesDay,
    ModelsResponse,
    ModelUsage,
    OpenPR,
    SpendDay,
    SpendResponse,
)
from .source import DataSource, SourceEmpty, SourceUnavailable

BILLING_LAG = timedelta(days=2)  # billing export lags up to a day; older than 2 days is stale
SNAPSHOT_LAG = timedelta(minutes=15)  # the collector runs every 5 minutes
SERVICE_KEYS = {"compute engine": "vm", "cloud run": "cloud_run", "vertex ai": "vertex_ai"}
MODEL_LABELS = {
    "opus": "Opus",
    "sonnet": "Sonnet",
    "haiku": "Haiku",
    "gemini_flash": "Gemini Flash",
    "other": "Other",
}
GUARD_RE = re.compile(r"cost=([\d.]+) budget=([\d.]+) -> (no action|agents suspended)")


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _iso_str(value: str | None) -> str | None:
    return iso(parse_ts(value)) if value else None


def model_class(model_id: str) -> str:
    m = model_id.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "gemini_flash" if "gemini" in m and "flash" in m else "other"


def spend(src: DataSource, days: int) -> SpendResponse:
    try:
        today = src.now().date()
        rows = src.billing_rows(today - timedelta(days=days - 1))
    except SourceEmpty as e:
        return SpendResponse(state="empty", message=str(e), days=days)
    except SourceUnavailable as e:
        return SpendResponse(state="unavailable", message=str(e), days=days)
    if not rows:
        return SpendResponse(state="empty", days=days, message="No billing data in this window.")
    by_day: dict[str, SpendDay] = {}
    totals = SpendDay(date="total")
    for r in rows:
        key = SERVICE_KEYS.get(r["service"].lower(), "other")
        for bucket in (by_day.setdefault(r["usage_date"], SpendDay(date=r["usage_date"])), totals):
            setattr(bucket, key, round(getattr(bucket, key) + r["cost_usd"], 6))
            bucket.total = round(bucket.total + r["cost_usd"], 6)
    series = [by_day[d] for d in sorted(by_day)]
    newest = datetime.fromisoformat(series[-1].date).replace(tzinfo=UTC)
    stale = src.now() - newest > BILLING_LAG
    return SpendResponse(
        state="lagging" if stale else "ok",
        as_of=iso(newest),
        message="Billing export is behind; showing the latest available day." if stale else None,
        days=days,
        series=series,
        totals=totals,
    )


def models(src: DataSource, hours: int) -> ModelsResponse:
    try:
        rows = src.monitoring_rows(hours)
    except SourceUnavailable as e:
        return ModelsResponse(state="unavailable", message=str(e), hours=hours)
    if not rows:
        return ModelsResponse(state="empty", hours=hours, message="No model usage in this window.")
    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        key = model_class(r["model_id"])
        agg[key][0] += r["tokens"]
        agg[key][1] += r["requests"]
    return ModelsResponse(
        state="ok",
        as_of=iso(src.now()),
        hours=hours,
        models=[
            ModelUsage(model=k, label=MODEL_LABELS[k], tokens=t, requests=n)
            for k, (t, n) in sorted(agg.items(), key=lambda kv: -kv[1][0])
        ],
    )


def agents(src: DataSource) -> AgentsResponse:
    try:
        rows = src.agent_snapshots()
    except SourceUnavailable as e:
        return AgentsResponse(state="unavailable", message=str(e))
    if not rows:
        return AgentsResponse(state="empty", message="No agent snapshots yet.")
    latest: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: parse_ts(r["ts"])):
        latest[r["name"]] = r
    as_of = max(parse_ts(r["ts"]) for r in latest.values())
    out = []
    for name in sorted(latest):
        r = latest[name]
        started = parse_ts(r["started_at"]) if r.get("started_at") else None
        end = as_of if r.get("phase") == "running" else None
        if end is None and r.get("last_activity"):
            end = parse_ts(r["last_activity"])
        fields = {k: v for k, v in r.items() if k != "ts"}
        for k in ("started_at", "last_activity"):
            fields[k] = _iso_str(fields.get(k))
        out.append(
            AgentRow(
                **{**fields, "limits_exceeded": bool(r.get("limits_exceeded"))},
                running_seconds=int((end - started).total_seconds()) if started and end else None,
            )
        )
    lead = next((a for a in out if a.role == "lead"), None)
    stale = src.now() - as_of > SNAPSHOT_LAG
    return AgentsResponse(
        state="lagging" if stale else "ok",
        as_of=iso(as_of),
        message="Collector snapshots are stale." if stale else None,
        agents=out,
        lead_last_activity=_iso_str(lead.last_activity) if lead else None,
        limits_exceeded_count=sum(a.limits_exceeded for a in out),
    )


def _counts(items: list[dict], key: str) -> list[Count]:
    return [Count(key=i[key], count=i["count"]) for i in items or []]


def github(src: DataSource) -> GithubResponse:
    try:
        rows = src.github_snapshots()
        merges = src.merges_per_day(14)
    except SourceUnavailable as e:
        return GithubResponse(state="unavailable", message=str(e))
    if not rows:
        return GithubResponse(state="empty", message="No GitHub snapshots yet.")
    r = max(rows, key=lambda r: parse_ts(r["ts"]))
    ts = parse_ts(r["ts"])
    ci = _counts(r.get("ci_conclusions"), "conclusion")
    total = sum(c.count for c in ci)
    ok = sum(c.count for c in ci if c.key == "success")
    stale = src.now() - ts > SNAPSHOT_LAG
    return GithubResponse(
        state="lagging" if stale else "ok",
        as_of=iso(ts),
        message="Collector snapshots are stale." if stale else None,
        snapshot=GithubSnapshot(
            ts=iso(ts),
            issues_by_status=_counts(r.get("issues_by_status"), "status"),
            issues_by_label=_counts(r.get("issues_by_label"), "label"),
            open_prs=[
                OpenPR(**{**p, "created_at": _iso_str(p.get("created_at"))})
                for p in r.get("open_prs") or []
            ],
            merges_24h=r["merges_24h"],
            ci_conclusions=ci,
            ci_pass_rate=ok / total if total else None,
        ),
        merges_per_day=[MergesDay(**m) for m in merges],
    )


def budget(src: DataSource, budget_usd: float) -> BudgetResponse:
    try:
        first = src.now().date().replace(day=1)
        rows = src.billing_rows(first)
        logs = src.guard_logs(20)
    except SourceEmpty as e:
        return BudgetResponse(state="empty", message=str(e), budget_usd=budget_usd)
    except SourceUnavailable as e:
        return BudgetResponse(state="unavailable", message=str(e), budget_usd=budget_usd)
    if not rows:
        return BudgetResponse(
            state="empty", budget_usd=budget_usd, message="No spend recorded this month."
        )
    mtd = round(sum(r["cost_usd"] for r in rows), 2)
    newest = max(date.fromisoformat(r["usage_date"]) for r in rows)
    newest_dt = datetime(newest.year, newest.month, newest.day, tzinfo=UTC)
    stale = src.now() - newest_dt > BILLING_LAG
    actions = []
    for line in logs:
        m = GUARD_RE.search(line["text"])
        if m:
            actions.append(
                GuardAction(
                    ts=_iso_str(line["ts"]),
                    cost_usd=float(m[1]),
                    budget_usd=float(m[2]),
                    action=m[3],
                )
            )
    actions.sort(key=lambda a: a.ts, reverse=True)
    return BudgetResponse(
        state="lagging" if stale else "ok",
        as_of=iso(newest_dt),
        message="Billing export is behind; month-to-date may be understated." if stale else None,
        budget_usd=budget_usd,
        mtd_usd=mtd,
        percent=round(mtd / budget_usd * 100, 2),
        markers=[
            Marker(percent=p, usd=budget_usd * p / 100, reached=mtd >= budget_usd * p / 100)
            for p in (50, 90, 100)
        ],  # fmt: skip
        guard_actions=actions,
    )
