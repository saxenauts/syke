from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from syke.config import ASK_TIMEOUT
from syke.llm import pi_runtime
from syke.llm.backends import AskEvent

MetadataValue = str | int | float | bool | None
AskMetadata = dict[str, MetadataValue]
RunAskFn = Callable[[object, str, str], tuple[str, AskMetadata]]
RunAskStreamFn = Callable[
    [object, str, str, Callable[[AskEvent], None] | None],
    tuple[str, AskMetadata],
]

RUN_ASK = cast(RunAskFn, pi_runtime.run_ask)
RUN_ASK_STREAM = cast(RunAskStreamFn, pi_runtime.run_ask_stream)


def _install_fake_module(module_name: str, **attrs: object) -> None:
    module = types.ModuleType(module_name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[module_name] = module


@pytest.fixture(autouse=True)
def _restore_backend_modules():
    targets = (
        "syke.llm.backends.pi_ask",
        "syke.llm.backends.pi_synthesis",
    )
    original = {name: sys.modules.get(name) for name in targets}
    yield
    for name, module in original.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _canonical_ask_metadata(**overrides: MetadataValue) -> AskMetadata:
    metadata: AskMetadata = {
        "backend": "pi",
        "cost_usd": None,
        "duration_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "tool_calls": None,
        "num_turns": None,
        "error": None,
    }
    metadata.update(overrides)
    return metadata


def test_run_ask_routes_to_pi_backend() -> None:
    called = {"pi": 0}
    captured: dict[str, str] = {}

    def fake_pi_ask(db: object, user_id: str, question: str, **kwargs: object):
        del db, user_id, kwargs
        called["pi"] += 1
        captured["question"] = question
        return "answer from pi", _canonical_ask_metadata(tool_calls=2)

    _install_fake_module("syke.llm.backends.pi_ask", pi_ask=fake_pi_ask)
    answer_text, metadata = RUN_ASK(object(), "user", "question")

    assert answer_text == "answer from pi"
    assert metadata["backend"] == "pi"
    assert metadata["tool_calls"] == 2
    assert called == {"pi": 1}
    assert "<operation_contract>" in captured["question"]
    assert "<synthesis>" not in captured["question"]
    assert "User question: question" in captured["question"]


def test_run_ask_prefers_daemon_ipc_when_available(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "syke.db"
    syke_db_path.write_text("", encoding="utf-8")

    with (
        patch(
            "syke.daemon.ipc.ask_via_daemon",
            return_value=("answer from daemon", _canonical_ask_metadata(tool_calls=1)),
        ) as daemon_mock,
        patch("syke.llm.backends.pi_ask.pi_ask") as pi_mock,
    ):
        answer_text, metadata = RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
            ),
            "user",
            "question",
        )

    assert answer_text == "answer from daemon"
    assert metadata["tool_calls"] == 1
    daemon_mock.assert_called_once()
    assert daemon_mock.call_args.kwargs["timeout"] == float(ASK_TIMEOUT)
    pi_mock.assert_not_called()


def test_run_ask_uses_daemon_ipc_for_persistent_path_before_file_exists(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "new-syke.db"
    assert not syke_db_path.exists()

    with (
        patch(
            "syke.daemon.ipc.ask_via_daemon",
            return_value=("answer from daemon", _canonical_ask_metadata(tool_calls=1)),
        ) as daemon_mock,
        patch("syke.llm.backends.pi_ask.pi_ask") as pi_mock,
    ):
        answer_text, metadata = RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
            ),
            "user",
            "question",
        )

    assert answer_text == "answer from daemon"
    assert metadata["tool_calls"] == 1
    daemon_mock.assert_called_once()
    assert daemon_mock.call_args.kwargs["syke_db_path"] == str(syke_db_path)
    pi_mock.assert_not_called()


def test_run_ask_requires_daemon_ipc_for_persistent_db(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "syke.db"
    syke_db_path.write_text("", encoding="utf-8")

    with (
        patch(
            "syke.daemon.ipc.ask_via_daemon",
            side_effect=RuntimeError("socket missing"),
        ),
        patch("syke.llm.backends.pi_ask.pi_ask") as pi_mock,
        pytest.raises(RuntimeError, match="socket missing"),
    ):
        RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
            ),
            "user",
            "question",
        )

    pi_mock.assert_not_called()


def test_run_ask_sends_busy_daemon_to_ipc_without_direct_bypass(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "syke.db"
    syke_db_path.write_text("", encoding="utf-8")

    with (
        patch(
            "syke.daemon.ipc.daemon_runtime_status",
            return_value={"alive": True, "busy": True, "provider": "kimi-coding"},
        ) as runtime_status_mock,
        patch(
            "syke.daemon.ipc.ask_via_daemon",
            return_value=(
                "answer from daemon worker",
                _canonical_ask_metadata(transport="daemon_worker"),
            ),
        ) as daemon_mock,
        patch("syke.llm.backends.pi_ask.pi_ask") as pi_mock,
    ):
        answer_text, metadata = RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
            ),
            "user",
            "question",
        )

    assert answer_text == "answer from daemon worker"
    assert metadata["transport"] == "daemon_worker"
    runtime_status_mock.assert_not_called()
    daemon_mock.assert_called_once()
    pi_mock.assert_not_called()


def test_run_ask_propagates_daemon_errors_without_direct_fallback(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "syke.db"
    syke_db_path.write_text("", encoding="utf-8")

    with (
        patch(
            "syke.daemon.ipc.ask_via_daemon",
            side_effect=RuntimeError("daemon busy: runtime in use"),
        ),
        patch("syke.llm.backends.pi_ask.pi_ask") as pi_mock,
        pytest.raises(RuntimeError, match="daemon busy"),
    ):
        RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
            ),
            "user",
            "question",
        )

    pi_mock.assert_not_called()


def test_run_ask_stream_passes_on_event_callback() -> None:
    seen: list[str] = []

    def fake_pi_ask(
        db: object,
        user_id: str,
        question: str,
        **kwargs: object,
    ):
        del db, user_id, question
        callback = cast(Callable[[AskEvent], None] | None, kwargs.get("on_event"))
        if callback is not None:
            callback(AskEvent(type="text", content="hello"))
        return "hello", _canonical_ask_metadata()

    _install_fake_module("syke.llm.backends.pi_ask", pi_ask=fake_pi_ask)
    answer_text, metadata = RUN_ASK_STREAM(
        object(),
        "user",
        "question",
        on_event=lambda event: seen.append(event.content),
    )

    assert answer_text == "hello"
    assert metadata["backend"] == "pi"
    assert seen == ["hello"]
