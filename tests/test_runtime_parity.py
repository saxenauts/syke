from __future__ import annotations

import types
import sys
from pathlib import Path
from collections.abc import Callable
from typing import cast

import pytest

from syke.llm import runtime_switch

CANONICAL_BACKENDS = {"claude", "pi"}
CANONICAL_SYNTHESIS_STATUSES = {"completed", "skipped", "failed"}

ASK_METADATA_KEYS = {
    "backend",
    "cost_usd",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "tool_calls",
    "error",
}

SYNTHESIS_RESULT_KEYS = {
    "backend",
    "status",
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "duration_ms",
    "events_processed",
    "memex_updated",
    "error",
    "reason",
}

MetadataValue = str | int | float | bool | None
AskMetadata = dict[str, MetadataValue]
SynthesisResult = dict[str, MetadataValue]

RunAskFn = Callable[[object, str, str], tuple[str, AskMetadata]]
RunSynthesisFn = Callable[..., SynthesisResult]
RUN_ASK = cast(RunAskFn, getattr(runtime_switch, "run_ask"))
RUN_SYNTHESIS = cast(RunSynthesisFn, getattr(runtime_switch, "run_synthesis"))


def _install_fake_module(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    **attrs: object,
) -> None:
    module = types.ModuleType(module_name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, module_name, module)


def _write_runtime_backend_config(tmp_path: Path, backend_value: str) -> None:
    cfg_dir = tmp_path / ".syke"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    _ = (cfg_dir / "config.toml").write_text(
        f'[runtime]\nbackend = "{backend_value}"\n',
        encoding="utf-8",
    )


def _canonical_ask_metadata(backend: str, **overrides: MetadataValue) -> AskMetadata:
    metadata: AskMetadata = {
        "backend": backend,
        "cost_usd": None,
        "duration_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "tool_calls": None,
        "error": None,
    }
    metadata.update(overrides)
    return metadata


def _canonical_synthesis_result(
    status: str,
    backend: str,
    **overrides: MetadataValue,
) -> SynthesisResult:
    result: SynthesisResult = {
        "backend": backend,
        "status": status,
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "duration_ms": None,
        "events_processed": None,
        "memex_updated": None,
        "error": None,
        "reason": None,
    }
    result.update(overrides)
    return result


def _assert_ask_contract(answer_text: str, metadata: AskMetadata, expected_backend: str) -> None:
    assert isinstance(answer_text, str)
    assert set(metadata.keys()) == ASK_METADATA_KEYS
    assert metadata["backend"] == expected_backend
    assert metadata["backend"] in CANONICAL_BACKENDS


def _assert_synthesis_contract(result: SynthesisResult, expected_backend: str) -> None:
    assert set(result.keys()) == SYNTHESIS_RESULT_KEYS
    assert result["backend"] == expected_backend
    assert result["backend"] in CANONICAL_BACKENDS
    assert result["status"] in CANONICAL_SYNTHESIS_STATUSES


class TestBackendSelectionContract:
    @pytest.mark.parametrize("env_backend", ["claude", "pi"])
    def test_get_runtime_prefers_valid_env_backend(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        env_backend: str,
    ) -> None:
        _write_runtime_backend_config(tmp_path, "claude")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("SYKE_RUNTIME", env_backend)

        assert runtime_switch.get_runtime() == env_backend

    def test_get_runtime_ignores_invalid_env_backend_and_uses_config_backend(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_runtime_backend_config(tmp_path, "pi")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("SYKE_RUNTIME", "unsupported-backend")

        assert runtime_switch.get_runtime() == "pi"

    def test_get_runtime_defaults_to_claude_when_config_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("SYKE_RUNTIME", raising=False)

        assert runtime_switch.get_runtime() == "claude"


class TestAskContract:
    @pytest.mark.parametrize("configured_backend", ["claude", "pi"])
    def test_run_ask_routes_to_configured_backend_and_returns_canonical_tuple(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        configured_backend: str,
    ) -> None:
        _write_runtime_backend_config(tmp_path, configured_backend)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("SYKE_RUNTIME", raising=False)

        called = {"claude": 0, "pi": 0}

        def fake_claude_ask(
            db: object, user_id: str, question: str, **kwargs: object
        ) -> tuple[str, AskMetadata]:
            del db, user_id, question, kwargs
            called["claude"] += 1
            return "answer from claude", _canonical_ask_metadata("claude", input_tokens=120)

        def fake_pi_ask(
            db: object, user_id: str, question: str, **kwargs: object
        ) -> tuple[str, AskMetadata]:
            del db, user_id, question, kwargs
            called["pi"] += 1
            return "answer from pi", _canonical_ask_metadata("pi", tool_calls=2)

        _install_fake_module(monkeypatch, "syke.distribution.ask_agent", ask=fake_claude_ask)
        _install_fake_module(monkeypatch, "syke.distribution.pi_ask", pi_ask=fake_pi_ask)

        answer_text, metadata = RUN_ASK(object(), "user", "question")
        _assert_ask_contract(answer_text, metadata, expected_backend=configured_backend)

        assert called[configured_backend] == 1
        assert called["pi" if configured_backend == "claude" else "claude"] == 0

    def test_invalid_config_backend_routes_run_ask_to_claude_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_runtime_backend_config(tmp_path, "not-a-runtime")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("SYKE_RUNTIME", raising=False)

        called = {"claude": 0, "pi": 0}

        def fake_claude_ask(
            db: object, user_id: str, question: str, **kwargs: object
        ) -> tuple[str, AskMetadata]:
            del db, user_id, question, kwargs
            called["claude"] += 1
            return "fallback answer", _canonical_ask_metadata("claude")

        def fake_pi_ask(
            db: object, user_id: str, question: str, **kwargs: object
        ) -> tuple[str, AskMetadata]:
            del db, user_id, question, kwargs
            called["pi"] += 1
            return "unexpected", _canonical_ask_metadata("pi")

        _install_fake_module(monkeypatch, "syke.distribution.ask_agent", ask=fake_claude_ask)
        _install_fake_module(monkeypatch, "syke.distribution.pi_ask", pi_ask=fake_pi_ask)

        answer_text, metadata = RUN_ASK(object(), "user", "question")
        _assert_ask_contract(answer_text, metadata, expected_backend="claude")

        assert called == {"claude": 1, "pi": 0}

    def test_invalid_env_backend_does_not_override_valid_config_for_run_ask(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_runtime_backend_config(tmp_path, "pi")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("SYKE_RUNTIME", "invalid-env-runtime")

        called = {"claude": 0, "pi": 0}

        def fake_claude_ask(
            db: object, user_id: str, question: str, **kwargs: object
        ) -> tuple[str, AskMetadata]:
            del db, user_id, question, kwargs
            called["claude"] += 1
            return "wrong path", _canonical_ask_metadata("claude")

        def fake_pi_ask(
            db: object, user_id: str, question: str, **kwargs: object
        ) -> tuple[str, AskMetadata]:
            del db, user_id, question, kwargs
            called["pi"] += 1
            return "pi path", _canonical_ask_metadata("pi")

        _install_fake_module(monkeypatch, "syke.distribution.ask_agent", ask=fake_claude_ask)
        _install_fake_module(monkeypatch, "syke.distribution.pi_ask", pi_ask=fake_pi_ask)

        answer_text, metadata = RUN_ASK(object(), "user", "question")
        _assert_ask_contract(answer_text, metadata, expected_backend="pi")

        assert called == {"claude": 0, "pi": 1}


class TestSynthesisContract:
    @pytest.mark.parametrize(
        ("status", "expected_reason", "expected_error"),
        [
            ("completed", None, None),
            ("skipped", "below_threshold", None),
            ("failed", None, "runtime failed"),
        ],
    )
    def test_canonical_synthesis_statuses_are_restricted_and_validated(
        self,
        status: str,
        expected_reason: str | None,
        expected_error: str | None,
    ) -> None:
        result = _canonical_synthesis_result(
            status=status,
            backend="claude",
            reason=expected_reason,
            error=expected_error,
        )
        _assert_synthesis_contract(result, expected_backend="claude")

    @pytest.mark.parametrize("configured_backend", ["claude", "pi"])
    def test_run_synthesis_routes_to_configured_backend_and_returns_canonical_dict(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        configured_backend: str,
    ) -> None:
        _write_runtime_backend_config(tmp_path, configured_backend)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("SYKE_RUNTIME", raising=False)

        called = {"claude": 0, "pi": 0}

        def fake_claude_synthesize(
            db: object,
            user_id: str,
            **kwargs: object,
        ) -> SynthesisResult:
            del db, user_id, kwargs
            called["claude"] += 1
            return _canonical_synthesis_result(
                status="completed",
                backend="claude",
                cost_usd=0.25,
                input_tokens=500,
                output_tokens=240,
                duration_ms=1900,
                events_processed=8,
                memex_updated=True,
            )

        def fake_pi_synthesize(
            db: object,
            user_id: str,
            **kwargs: object,
        ) -> SynthesisResult:
            del db, user_id, kwargs
            called["pi"] += 1
            return _canonical_synthesis_result(
                status="skipped",
                backend="pi",
                reason="below_threshold",
            )

        _install_fake_module(
            monkeypatch, "syke.memory.synthesis", synthesize=fake_claude_synthesize
        )
        _install_fake_module(
            monkeypatch, "syke.memory.pi_synthesis", pi_synthesize=fake_pi_synthesize
        )

        result = RUN_SYNTHESIS(object(), "user", force=False, skill_override=None)
        _assert_synthesis_contract(result, expected_backend=configured_backend)

        assert called[configured_backend] == 1
        assert called["pi" if configured_backend == "claude" else "claude"] == 0

    def test_invalid_config_backend_routes_run_synthesis_to_claude_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_runtime_backend_config(tmp_path, "broken-backend")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("SYKE_RUNTIME", raising=False)

        called = {"claude": 0, "pi": 0}

        def fake_claude_synthesize(
            db: object,
            user_id: str,
            **kwargs: object,
        ) -> SynthesisResult:
            del db, user_id, kwargs
            called["claude"] += 1
            return _canonical_synthesis_result(
                status="failed", backend="claude", error="runtime failed"
            )

        def fake_pi_synthesize(
            db: object,
            user_id: str,
            **kwargs: object,
        ) -> SynthesisResult:
            del db, user_id, kwargs
            called["pi"] += 1
            return _canonical_synthesis_result(status="completed", backend="pi")

        _install_fake_module(
            monkeypatch, "syke.memory.synthesis", synthesize=fake_claude_synthesize
        )
        _install_fake_module(
            monkeypatch, "syke.memory.pi_synthesis", pi_synthesize=fake_pi_synthesize
        )

        result = RUN_SYNTHESIS(object(), "user")
        _assert_synthesis_contract(result, expected_backend="claude")

        assert called == {"claude": 1, "pi": 0}

    def test_invalid_env_backend_does_not_override_valid_config_for_run_synthesis(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_runtime_backend_config(tmp_path, "pi")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("SYKE_RUNTIME", "unknown-backend")

        called = {"claude": 0, "pi": 0}

        def fake_claude_synthesize(
            db: object,
            user_id: str,
            **kwargs: object,
        ) -> SynthesisResult:
            del db, user_id, kwargs
            called["claude"] += 1
            return _canonical_synthesis_result(status="completed", backend="claude")

        def fake_pi_synthesize(
            db: object,
            user_id: str,
            **kwargs: object,
        ) -> SynthesisResult:
            del db, user_id, kwargs
            called["pi"] += 1
            return _canonical_synthesis_result(status="completed", backend="pi")

        _install_fake_module(
            monkeypatch, "syke.memory.synthesis", synthesize=fake_claude_synthesize
        )
        _install_fake_module(
            monkeypatch, "syke.memory.pi_synthesis", pi_synthesize=fake_pi_synthesize
        )

        result = RUN_SYNTHESIS(object(), "user")
        _assert_synthesis_contract(result, expected_backend="pi")

        assert called == {"claude": 0, "pi": 1}
