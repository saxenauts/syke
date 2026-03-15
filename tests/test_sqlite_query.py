
from syke.ingestion.sqlite_query import SQLiteQueryAdapter


def test_sqlite_stub_instantiates(db, user_id):
    adapter = SQLiteQueryAdapter(db, user_id, source_name="cursor")
    assert adapter.source == "cursor"
    assert adapter.discover() == []
    assert list(adapter.iter_sessions()) == []
