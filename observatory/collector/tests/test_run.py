from collector.__main__ import main, run
from collector.schema import TABLES, validate_row
from collector.sink import FakeSink


def test_run_writes_one_shared_ts_and_valid_rows(monkeypatch):
    monkeypatch.setenv("OBSERVATORY_DATA", "fake")
    sink = FakeSink()
    run(sink, "fake")
    stamps = {r["ts"] for rows in sink.rows.values() for r in rows}
    assert len(stamps) == 1
    for table in TABLES:
        assert sink.rows[table.name]
        for row in sink.rows[table.name]:
            validate_row(table, row)


def test_source_failure_is_reported_but_other_sources_still_write():
    sink = FakeSink()

    def boom(ts):
        raise RuntimeError("hub down")

    ok = run(
        sink, "fake", sources={"agent_snapshots": boom, "github_snapshots": lambda ts: [{"ts": ts}]}
    )
    assert ok is False
    assert sink.rows == {"github_snapshots": [{"ts": sink.rows["github_snapshots"][0]["ts"]}]}


def test_main_fake_exits_zero(monkeypatch):
    monkeypatch.setenv("OBSERVATORY_DATA", "fake")
    assert main() == 0
