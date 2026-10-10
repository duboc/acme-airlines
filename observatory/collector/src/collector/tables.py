"""Idempotent table creation."""

from google.cloud import bigquery

from .schema import TABLES


def ensure_tables(client: bigquery.Client, project: str, dataset: str) -> None:
    """Create each table if missing. Existing tables are never altered or dropped."""
    for t in TABLES:
        table = bigquery.Table(f"{project}.{dataset}.{t.name}", schema=list(t.fields))
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, field=t.partition_field
        )
        client.create_table(table, exists_ok=True)
