from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from syke.cli_support.doctor import build_doctor_payload


def test_build_doctor_payload_keeps_trace_store_checks_distinct(
    tmp_path: Path, monkeypatch
) -> None:
    from syke.cli_support import doctor

    monkeypatch.setattr(
        "syke.config.user_syke_db_path",
        lambda _user_id: tmp_path / "missing-syke.db",
    )

    pi_bin = tmp_path / "pi"
    pi_bin.write_text("pi", encoding="utf-8")
    monkeypatch.setattr(doctor, "PI_BIN", pi_bin)
    monkeypatch.setattr(
        doctor,
        "resolve_provider",
        lambda cli_provider=None: SimpleNamespace(id="openai"),
    )
    monkeypatch.setattr(doctor, "resolve_source", lambda _provider: "default")
    monkeypatch.setattr(
        doctor,
        "build_pi_runtime_env",
        lambda _provider: {"OPENAI_API_KEY": "sk-test"},
    )
    monkeypatch.setattr(
        doctor,
        "describe_provider",
        lambda _provider_id, selection_source=None: {"endpoint": "provider default"},
    )
    monkeypatch.setattr(
        doctor,
        "evaluate_provider_readiness",
        lambda _provider_id: SimpleNamespace(ready=True, detail="ready"),
    )
    monkeypatch.setattr(doctor, "get_pi_version", lambda **_kwargs: "1.2.3")
    monkeypatch.setattr(doctor, "resolve_syke_runtime", lambda: "current-runtime")
    monkeypatch.setattr(doctor, "resolve_background_syke_runtime", lambda: "background-runtime")
    monkeypatch.setattr(doctor, "describe_runtime_target", lambda runtime: str(runtime))
    monkeypatch.setattr(doctor, "is_running", lambda: (False, None))
    monkeypatch.setattr(doctor, "launchd_metadata", lambda: {"registered": False})
    monkeypatch.setattr(doctor, "daemon_ipc_status", lambda _user_id: {"ok": True, "detail": "ok"})
    monkeypatch.setattr(
        doctor,
        "trace_store_status",
        lambda _user_id: {"ok": True, "detail": "rollout traces available"},
    )
    monkeypatch.setattr(
        doctor,
        "runtime_metrics_status",
        lambda _user_id: {
            "file_logging": {"ok": True, "detail": "file logging ok"},
            "trace_store": {"ok": True, "detail": "runtime trace store writable"},
        },
    )

    ctx = SimpleNamespace(obj={"user": "test", "provider": None})
    payload = build_doctor_payload(ctx, network=False)
    checks = payload["checks"]

    assert "trace_store" in checks
    assert "trace_store_runtime" in checks
    assert checks["trace_store"]["label"] == "Rollout traces"
    assert checks["trace_store_runtime"]["label"] == "Trace store runtime"
    assert checks["trace_store"]["detail"] == "rollout traces available"
    assert checks["trace_store_runtime"]["detail"] == "runtime trace store writable"
