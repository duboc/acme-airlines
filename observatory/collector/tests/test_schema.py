import json
from pathlib import Path

import pytest

from collector.schema import (
    AGENT_SNAPSHOTS,
    GITHUB_SNAPSHOTS,
    TABLES,
    SchemaError,
    validate_row,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_agent_snapshot_columns():
    names = [f.name for f in AGENT_SNAPSHOTS.fields]
    assert names == [
        "ts", "name", "template", "role", "tier", "phase", "activity", "container_status",
        "started_at", "last_activity", "harness", "model", "limits_exceeded",
    ]  # fmt: skip
    types = {f.name: f.field_type for f in AGENT_SNAPSHOTS.fields}
    assert types["ts"] == "TIMESTAMP"
    assert types["limits_exceeded"] == "BOOL"


def test_github_snapshot_has_documented_shape():
    names = {f.name for f in GITHUB_SNAPSHOTS.fields}
    assert {"ts", "issues_by_status", "issues_by_label", "open_prs", "merges_24h",
            "ci_conclusions"} <= names  # fmt: skip


def test_tables_are_partitioned_by_ts_day():
    assert {t.name for t in TABLES} == {"agent_snapshots", "github_snapshots"}
    assert all(t.partition_field == "ts" for t in TABLES)


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_fixtures_conform(table):
    rows = json.loads((FIXTURES / f"{table.name}.json").read_text())
    assert rows
    for row in rows:
        validate_row(table, row)


def test_validate_rejects_unknown_missing_and_bad_types():
    row = json.loads((FIXTURES / "agent_snapshots.json").read_text())[0]
    with pytest.raises(SchemaError, match="unknown"):
        validate_row(AGENT_SNAPSHOTS, {**row, "bogus": 1})
    with pytest.raises(SchemaError, match="ts"):
        validate_row(AGENT_SNAPSHOTS, {k: v for k, v in row.items() if k != "ts"})
    with pytest.raises(SchemaError, match="limits_exceeded"):
        validate_row(AGENT_SNAPSHOTS, {**row, "limits_exceeded": "yes"})
    with pytest.raises(SchemaError, match="started_at"):
        validate_row(AGENT_SNAPSHOTS, {**row, "started_at": "not-a-time"})


def test_validate_nested_repeated():
    row = json.loads((FIXTURES / "github_snapshots.json").read_text())[0]
    bad = {**row, "issues_by_status": [{"status": "ready"}]}
    with pytest.raises(SchemaError, match="count"):
        validate_row(GITHUB_SNAPSHOTS, bad)
