from pathlib import Path

from syke.cli import _claude_is_authenticated


def test_claude_auth_false_when_binary_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: None)

    assert _claude_is_authenticated() is False


def test_claude_auth_false_when_claude_dir_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/claude")

    assert _claude_is_authenticated() is False


def test_claude_auth_true_when_json_auth_file_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/claude")

    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "credentials.json").write_text("{}")

    assert _claude_is_authenticated() is True
