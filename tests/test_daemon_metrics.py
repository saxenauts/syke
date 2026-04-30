from __future__ import annotations

from pathlib import Path

from syke.daemon.metrics import run_health_check


class _FakeCursor:
    def __init__(self, value: int) -> None:
        self._value = value

    def fetchone(self):
        return [self._value]


class _FakeConn:
    def execute(self, _query: str, _params):
        return _FakeCursor(0)


class _FakeDB:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def initialize(self) -> None:
        return None

    def count_memories(self, _user_id: str, *, active_only: bool = True) -> int:
        return 0

    def get_memex(self, _user_id: str):
        return {}

    def close(self) -> None:
        return None


def test_run_health_check_marks_unhealthy_when_runtime_not_alive(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("syke.daemon.metrics.user_syke_db_path", lambda _user: tmp_path / "syke.db")
    monkeypatch.setattr("syke.daemon.metrics.user_data_dir", lambda _user: tmp_path)
    monkeypatch.setattr("syke.db.SykeDB", lambda _path: _FakeDB())
    monkeypatch.setattr("syke.health.synthesis_health", lambda _db, _user: {"assessment": "active"})
    monkeypatch.setattr("syke.health.signals", lambda _db, _user: [])
    monkeypatch.setattr(
        "syke.daemon.ipc.daemon_runtime_status",
        lambda _user, timeout=0.5: {"alive": False, "detail": "runtime down"},
    )

    health = run_health_check("test")

    assert health["checks"]["runtime"]["ok"] is False
    assert health["healthy"] is False


def test_run_health_check_is_healthy_when_runtime_is_alive(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.metrics.user_syke_db_path", lambda _user: tmp_path / "syke.db")
    monkeypatch.setattr("syke.daemon.metrics.user_data_dir", lambda _user: tmp_path)
    monkeypatch.setattr("syke.db.SykeDB", lambda _path: _FakeDB())
    monkeypatch.setattr("syke.health.synthesis_health", lambda _db, _user: {"assessment": "active"})
    monkeypatch.setattr("syke.health.signals", lambda _db, _user: [])
    monkeypatch.setattr(
        "syke.daemon.ipc.daemon_runtime_status",
        lambda _user, timeout=0.5: {"alive": True, "detail": "runtime alive"},
    )

    health = run_health_check("test")

    assert health["checks"]["runtime"]["ok"] is True
    assert health["healthy"] is True
