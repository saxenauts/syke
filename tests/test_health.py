from syke import health


def test_hours_ago_handles_naive_iso():
    hours = health._hours_ago("2026-03-27 10:00:00")
    assert isinstance(hours, float)
    assert hours > 0


def test_ingestion_health_surfaces_naive_timestamps():
    class DummyDB:
        def get_ingestion_staleness(self, user_id):
            return [{"source": "test-source", "count": 1, "last_sync": "2026-03-27 10:00:00"}]

    result = health.ingestion_health(DummyDB(), "user")
    source = result["sources"][0]
    assert source["last_sync_hours"] is not None
    assert source["last_sync_ago"] != "never"
    assert source["status"] != "unknown"
