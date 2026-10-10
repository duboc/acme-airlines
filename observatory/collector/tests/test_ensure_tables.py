from unittest.mock import MagicMock

from collector.schema import TABLES
from collector.tables import ensure_tables


def test_creates_each_table_if_not_exists_partitioned():
    client = MagicMock()
    ensure_tables(client, "proj", "factory_ops")
    client.create_dataset.assert_not_called()
    assert client.create_table.call_count == len(TABLES)
    for call in client.create_table.call_args_list:
        table = call.args[0]
        assert table.dataset_id == "factory_ops"
        assert table.project == "proj"
        assert table.time_partitioning.field == "ts"
        assert table.time_partitioning.type_ == "DAY"
        assert call.kwargs == {"exists_ok": True}


def test_idempotent_never_deletes_or_updates():
    client = MagicMock()
    ensure_tables(client, "proj", "factory_ops")
    ensure_tables(client, "proj", "factory_ops")
    client.delete_table.assert_not_called()
    client.update_table.assert_not_called()
