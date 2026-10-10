"""BigQuery schemas for the ``factory_ops`` dataset (the shared Observatory contract)."""

from dataclasses import dataclass
from datetime import datetime

from google.cloud.bigquery import SchemaField as F


class SchemaError(ValueError):
    """A row does not conform to its table schema."""


@dataclass(frozen=True)
class Table:
    name: str
    fields: tuple[F, ...]
    partition_field: str = "ts"  # DAY partitioning, i.e. DATE(ts)


def _counts(name: str, key: str, description: str) -> F:
    return F(
        name,
        "RECORD",
        mode="REPEATED",
        description=description,
        fields=(F(key, "STRING", mode="REQUIRED"), F("count", "INT64", mode="REQUIRED")),
    )


AGENT_SNAPSHOTS = Table(
    "agent_snapshots",
    (
        F("ts", "TIMESTAMP", mode="REQUIRED", description="Collector run time, shared by a run"),
        F("name", "STRING", mode="REQUIRED"),
        F("template", "STRING"),
        F("role", "STRING"),
        F("tier", "STRING"),
        F("phase", "STRING"),
        F("activity", "STRING"),
        F("container_status", "STRING"),
        F("started_at", "TIMESTAMP"),
        F("last_activity", "TIMESTAMP"),
        F("harness", "STRING"),
        F("model", "STRING"),
        F("limits_exceeded", "BOOL"),
    ),
)

GITHUB_SNAPSHOTS = Table(
    "github_snapshots",
    (
        F("ts", "TIMESTAMP", mode="REQUIRED", description="Collector run time, shared by a run"),
        _counts("issues_by_status", "status", "Open issues per status:* label"),
        _counts("issues_by_label", "label", "Open issues per label (role, priority, scope)"),
        F(
            "open_prs",
            "RECORD",
            mode="REPEATED",
            description="Open PRs with latest review verdict and CI conclusion",
            fields=(
                F("number", "INT64", mode="REQUIRED"),
                F("title", "STRING"),
                F("author", "STRING"),
                F("created_at", "TIMESTAMP"),
                F("latest_verdict", "STRING", description="APPROVE, REQUEST CHANGES or null"),
                F("ci_conclusion", "STRING", description="success, failure, pending or null"),
            ),
        ),
        F("merges_24h", "INT64", mode="REQUIRED", description="PRs merged in the last 24 hours"),
        _counts("ci_conclusions", "conclusion", "Workflow run conclusions, last 24 hours"),
    ),
)

TABLES: tuple[Table, ...] = (AGENT_SNAPSHOTS, GITHUB_SNAPSHOTS)


def _check_scalar(path: str, field_type: str, value: object) -> None:
    ok: bool
    match field_type:
        case "STRING":
            ok = isinstance(value, str)
        case "BOOL":
            ok = isinstance(value, bool)
        case "INT64":
            ok = isinstance(value, int) and not isinstance(value, bool)
        case "TIMESTAMP":
            try:
                ok = isinstance(value, str) and bool(datetime.fromisoformat(value))
            except ValueError:
                ok = False
        case _:
            raise SchemaError(f"{path}: unsupported type {field_type}")
    if not ok:
        raise SchemaError(f"{path}: expected {field_type}, got {value!r}")


def _validate(path: str, fields: tuple[F, ...], row: object) -> None:
    if not isinstance(row, dict):
        raise SchemaError(f"{path}: expected an object")
    known = {f.name for f in fields}
    for key in row:
        if key not in known:
            raise SchemaError(f"{path}{key}: unknown column")
    for f in fields:
        value = row.get(f.name)
        where = f"{path}{f.name}"
        if value is None:
            if f.mode == "REQUIRED":
                raise SchemaError(f"{where}: required")
            continue
        items = value if f.mode == "REPEATED" else [value]
        if f.mode == "REPEATED" and not isinstance(value, list):
            raise SchemaError(f"{where}: expected a list")
        for item in items:
            if f.field_type == "RECORD":
                _validate(f"{where}.", tuple(f.fields), item)
            else:
                _check_scalar(where, f.field_type, item)


def validate_row(table: Table, row: dict) -> None:
    """Raise SchemaError unless ``row`` (JSON form) conforms to ``table``."""
    _validate("", table.fields, row)
