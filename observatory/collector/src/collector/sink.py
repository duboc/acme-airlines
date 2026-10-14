"""Row sinks: BigQuery for real runs, in-memory for ``OBSERVATORY_DATA=fake``."""

import os
from typing import Protocol


class Sink(Protocol):
    def write(self, table: str, rows: list[dict]) -> None: ...


class FakeSink:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}

    def write(self, table: str, rows: list[dict]) -> None:
        self.rows.setdefault(table, []).extend(rows)


class BigQuerySink:
    def __init__(self, client, project: str, dataset: str) -> None:
        self._client = client
        self._project = project
        self._dataset = dataset

    def write(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        errors = self._client.insert_rows_json(f"{self._project}.{self._dataset}.{table}", rows)
        if errors:
            raise RuntimeError(f"insert into {table} failed: {errors}")


def make_sink() -> Sink:
    if os.environ.get("OBSERVATORY_DATA") == "fake":
        return FakeSink()
    from google.cloud import bigquery

    project = os.environ["PROJECT_ID"]
    return BigQuerySink(
        bigquery.Client(project=project), project, os.environ.get("OPS_DATASET", "factory_ops")
    )
