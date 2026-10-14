"""``python -m collector``: ensure tables, then snapshot every source with one shared ts."""

import logging
import os
import sys
from datetime import UTC, datetime

from .sink import BigQuerySink, Sink, make_sink
from .sources import Source, default_sources

log = logging.getLogger("collector")


def run(sink: Sink, mode: str, sources: dict[str, Source] | None = None) -> bool:
    """Write one row set sharing a single ts. Returns False if any source failed."""
    ts = datetime.now(UTC).isoformat()
    ok = True
    for table, source in (sources or default_sources(mode)).items():
        try:
            rows = source(ts)
            sink.write(table, rows)
            log.info("wrote %d rows to %s", len(rows), table)
        except Exception:
            log.exception("snapshot %s failed", table)
            ok = False
    return ok


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mode = "fake" if os.environ.get("OBSERVATORY_DATA") == "fake" else "live"
    if mode == "live":
        from google.cloud import bigquery

        from .tables import ensure_tables

        project = os.environ["PROJECT_ID"]
        dataset = os.environ.get("OPS_DATASET", "factory_ops")
        client = bigquery.Client(project=project)
        ensure_tables(client, project, dataset)
        sink: Sink = BigQuerySink(client, project, dataset)
    else:
        sink = make_sink()
    return 0 if run(sink, mode) else 1


if __name__ == "__main__":
    sys.exit(main())
