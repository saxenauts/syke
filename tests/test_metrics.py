import stat
from pathlib import Path

from syke.metrics import setup_logging


def test_setup_logging_creates_private_log_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.metrics.user_data_dir", lambda _user: tmp_path)

    setup_logging("test-user")

    log_path = tmp_path / "syke.log"
    assert log_path.exists()
    mode = stat.S_IMODE(log_path.stat().st_mode)
    assert mode == 0o600
