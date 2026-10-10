"""Real data source: BigQuery, Cloud Monitoring and Cloud Logging, read-only (ADC).

Every GCP failure is turned into SourceUnavailable (no details leaked); a missing billing
export is SourceEmpty; missing ``factory_ops`` tables read as no rows. Results are cached
for ``ttl`` seconds to bound BigQuery cost.
"""

import logging
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from functools import wraps

from google.api_core.exceptions import NotFound

from .source import Rows, SourceEmpty, SourceUnavailable

log = logging.getLogger(__name__)

BILLING_PREFIX = "gcp_billing_export_v1_"
TOKENS = "aiplatform.googleapis.com/publisher/online_serving/token_count"
REQUESTS = "aiplatform.googleapis.com/publisher/online_serving/model_invocation_count"
MODEL_LABEL = "model_user_id"  # resource label of aiplatform.googleapis.com/PublisherModel
GUARD_FILTER = (
    'resource.labels.service_name="acme-budget-guard" AND textPayload:"cost=" '
    'AND timestamp>="{since}"'
)
TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")


def jsonable(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [jsonable(x) for x in v]
    return v


def guarded(what: str):
    """Cache by (method, args) for ttl seconds; map GCP errors to SourceUnavailable."""

    def deco(fn):
        @wraps(fn)
        def wrapper(self, *args):
            key = (fn.__name__, args)
            hit = self._cache.get(key)
            if hit and self._monotonic() - hit[0] < self._ttl:
                return hit[1]
            try:
                value = fn(self, *args)
            except (SourceEmpty, SourceUnavailable):
                raise
            except Exception as e:
                log.warning("%s failed: %s", what, type(e).__name__)
                raise SourceUnavailable(f"{what} is unavailable right now.") from e
            self._cache[key] = (self._monotonic(), value)
            return value

        return wrapper

    return deco


class GcpDataSource:
    def __init__(
        self,
        project: str,
        bq,
        monitoring,
        logging_client,
        billing_dataset: str = "billing_export",
        ops_dataset: str = "factory_ops",
        ttl: float = 60.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._project, self._bq, self._mon, self._log = project, bq, monitoring, logging_client
        self._billing_ds, self._ops_ds = billing_dataset, ops_dataset
        self._ttl, self.now, self._monotonic = ttl, now, monotonic
        self._cache: dict = {}

    @classmethod
    def from_env(cls) -> "GcpDataSource":
        from google.cloud import bigquery, monitoring_v3
        from google.cloud import logging as cloud_logging

        project = os.environ["PROJECT_ID"]
        return cls(
            project,
            bigquery.Client(project=project),
            monitoring_v3.MetricServiceClient(),
            cloud_logging.Client(project=project),
            os.environ.get("BILLING_DATASET", "billing_export"),
            os.environ.get("OPS_DATASET", "factory_ops"),
        )

    def _query(self, sql: str, **params) -> Rows:
        from google.cloud import bigquery

        types = {date: "DATE"}
        cfg = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(k, types[type(v)], v) for k, v in params.items()
            ]
        )
        return [jsonable(dict(r)) for r in self._bq.query(sql, job_config=cfg).result()]

    def _ops(self, table: str, sql: str, **params) -> Rows:
        try:
            return self._query(sql.format(t=f"{self._project}.{self._ops_ds}.{table}"), **params)
        except NotFound:
            return []

    @guarded("Billing data")
    def billing_rows(self, start: date) -> Rows:
        try:
            ids = [t.table_id for t in self._bq.list_tables(f"{self._project}.{self._billing_ds}")]
        except NotFound:
            ids = []
        table = next((t for t in sorted(ids) if t.startswith(BILLING_PREFIX)), None)
        if not table or not TABLE_RE.match(table):
            raise SourceEmpty("Billing export not enabled yet: no export table found.")
        sql = (
            "SELECT DATE(usage_start_time) AS usage_date, service.description AS service, "
            "SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS cost_usd "
            f"FROM `{self._project}.{self._billing_ds}.{table}` "
            "WHERE usage_start_time >= TIMESTAMP(@start) GROUP BY 1, 2 ORDER BY 1"
        )
        return self._query(sql, start=start)

    @guarded("Agent snapshots")
    def agent_snapshots(self) -> Rows:
        return self._latest("agent_snapshots")

    @guarded("GitHub snapshots")
    def github_snapshots(self) -> Rows:
        return self._latest("github_snapshots")

    def _latest(self, table: str) -> Rows:
        day = self.now().date() - timedelta(days=1)
        sql = (
            "SELECT * FROM `{t}` WHERE DATE(ts) >= @day "
            "AND ts = (SELECT MAX(ts) FROM `{t}` WHERE DATE(ts) >= @day)"
        )
        return self._ops(table, sql, day=day)

    @guarded("GitHub history")
    def merges_per_day(self, days: int) -> Rows:
        sql = (
            "SELECT DATE(ts) AS date, MAX(merges_24h) AS count FROM `{t}` "
            "WHERE DATE(ts) >= @since GROUP BY 1 ORDER BY 1"
        )
        return self._ops("github_snapshots", sql, since=self.now().date() - timedelta(days=days))

    @guarded("Model usage")
    def monitoring_rows(self, hours: int) -> Rows:
        from google.cloud import monitoring_v3

        end = self.now()
        interval = monitoring_v3.TimeInterval(end_time=end, start_time=end - timedelta(hours=hours))
        agg = monitoring_v3.Aggregation(
            alignment_period={"seconds": hours * 3600},
            per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
            cross_series_reducer=monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
            group_by_fields=[f"resource.label.{MODEL_LABEL}"],
        )
        totals: dict[str, dict[str, int]] = {}
        for metric, field in ((TOKENS, "tokens"), (REQUESTS, "requests")):
            request = {
                "name": f"projects/{self._project}",
                "filter": f'metric.type="{metric}"',
                "interval": interval,
                "aggregation": agg,
            }
            for ts in self._mon.list_time_series(request=request):
                model = ts.resource.labels.get(MODEL_LABEL, "unknown")
                row = totals.setdefault(model, {"tokens": 0, "requests": 0})
                row[field] += sum(p.value.int64_value for p in ts.points)
        return [{"model_id": m, **v} for m, v in totals.items()]

    @guarded("Budget guard logs")
    def guard_logs(self, limit: int) -> Rows:
        since = (self.now() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries = self._log.list_entries(
            filter_=GUARD_FILTER.format(since=since),
            order_by="timestamp desc",
            max_results=limit,
            resource_names=[f"projects/{self._project}"],
        )
        return [
            {"ts": e.timestamp.isoformat(), "text": e.payload}
            for e in entries
            if isinstance(e.payload, str)
        ]
