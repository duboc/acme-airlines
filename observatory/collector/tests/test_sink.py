from unittest.mock import MagicMock

import pytest

from collector.sink import BigQuerySink, FakeSink, make_sink


def test_fake_sink_records_rows_per_table():
    sink = FakeSink()
    sink.write("agent_snapshots", [{"a": 1}])
    sink.write("agent_snapshots", [{"a": 2}])
    assert sink.rows["agent_snapshots"] == [{"a": 1}, {"a": 2}]


def test_bigquery_sink_inserts_and_raises_on_errors():
    client = MagicMock()
    client.insert_rows_json.return_value = []
    BigQuerySink(client, "p", "ds").write("agent_snapshots", [{"a": 1}])
    client.insert_rows_json.assert_called_once_with("p.ds.agent_snapshots", [{"a": 1}])
    client.insert_rows_json.return_value = [{"errors": ["boom"]}]
    with pytest.raises(RuntimeError, match="boom"):
        BigQuerySink(client, "p", "ds").write("agent_snapshots", [{"a": 1}])


def test_bigquery_sink_skips_empty():
    client = MagicMock()
    BigQuerySink(client, "p", "ds").write("agent_snapshots", [])
    client.insert_rows_json.assert_not_called()


def test_make_sink_fake_needs_no_gcp(monkeypatch):
    monkeypatch.setenv("OBSERVATORY_DATA", "fake")
    assert isinstance(make_sink(), FakeSink)
