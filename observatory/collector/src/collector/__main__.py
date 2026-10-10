"""``python -m collector``: ensure tables, then snapshot every source with one shared ts."""

import logging
import os
import sys
from datetime import UTC, datetime

from .sink import Sink, make_sink
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
        ensure_tables(
            bigquery.Client(project=project), project, os.environ.get("OPS_DATASET", "factory_ops")
        )
    return 0 if run(make_sink(), mode) else 1


if __name__ == "__main__":
    sys.exit(main())
