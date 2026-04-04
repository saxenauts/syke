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

    def fake_pi_ask(db: object, user_id: str, question: str, **kwargs: object):
        del db, user_id, question, kwargs
        called["pi"] += 1
        return "answer from pi", _canonical_ask_metadata(tool_calls=2)

    _install_fake_module("syke.llm.backends.pi_ask", pi_ask=fake_pi_ask)
    answer_text, metadata = RUN_ASK(object(), "user", "question")

    assert answer_text == "answer from pi"
    assert metadata["backend"] == "pi"
    assert metadata["tool_calls"] == 2
    assert called == {"pi": 1}


def test_run_ask_prefers_daemon_ipc_when_available(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "syke.db"
    event_db_path = tmp_path / "events.db"
    syke_db_path.write_text("", encoding="utf-8")
    event_db_path.write_text("", encoding="utf-8")

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
                event_db_path=str(event_db_path),
            ),
            "user",
            "question",
        )

    assert answer_text == "answer from daemon"
    assert metadata["tool_calls"] == 1
    daemon_mock.assert_called_once()
    assert daemon_mock.call_args.kwargs["timeout"] == float(ASK_TIMEOUT)
    pi_mock.assert_not_called()


def test_run_ask_falls_back_to_pi_when_daemon_unavailable(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "syke.db"
    event_db_path = tmp_path / "events.db"
    syke_db_path.write_text("", encoding="utf-8")
    event_db_path.write_text("", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_pi_ask(db: object, user_id: str, question: str, **kwargs: object):
        del db, user_id, question
        captured.update(kwargs)
        return "answer from pi", _canonical_ask_metadata()

    with patch(
        "syke.daemon.ipc.ask_via_daemon",
        side_effect=RuntimeError("socket missing"),
    ):
        _install_fake_module("syke.llm.backends.pi_ask", pi_ask=fake_pi_ask)
        answer_text, metadata = RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
                event_db_path=str(event_db_path),
            ),
            "user",
            "question",
        )

    assert answer_text == "answer from pi"
    assert metadata["backend"] == "pi"
    transport_details = cast(dict[str, object], captured["transport_details"])
    assert captured["timeout"] == float(ASK_TIMEOUT)
    assert transport_details["ipc_fallback"] is True
    assert "socket missing" in str(transport_details["ipc_error"])
    assert isinstance(transport_details["ipc_attempt_ms"], int)


def test_run_ask_bypasses_daemon_ipc_when_runtime_is_busy(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "syke.db"
    event_db_path = tmp_path / "events.db"
    syke_db_path.write_text("", encoding="utf-8")
    event_db_path.write_text("", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_pi_ask(db: object, user_id: str, question: str, **kwargs: object):
        del db, user_id, question
        captured.update(kwargs)
        return "answer from pi", _canonical_ask_metadata()

    with (
        patch(
            "syke.daemon.ipc.daemon_runtime_status",
            return_value={"alive": True, "busy": True, "provider": "kimi-coding"},
        ),
        patch("syke.daemon.ipc.ask_via_daemon") as daemon_mock,
    ):
        _install_fake_module("syke.llm.backends.pi_ask", pi_ask=fake_pi_ask)
        answer_text, metadata = RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
                event_db_path=str(event_db_path),
            ),
            "user",
            "question",
        )

    assert answer_text == "answer from pi"
    assert metadata["backend"] == "pi"
    daemon_mock.assert_not_called()
    transport_details = cast(dict[str, object], captured["transport_details"])
    assert transport_details["ipc_bypassed"] is True
    assert transport_details["ipc_bypass_reason"] == "daemon_busy"


def test_run_ask_falls_back_to_pi_when_daemon_races_busy(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "syke.db"
    event_db_path = tmp_path / "events.db"
    syke_db_path.write_text("", encoding="utf-8")
    event_db_path.write_text("", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_pi_ask(db: object, user_id: str, question: str, **kwargs: object):
        del db, user_id, question
        captured.update(kwargs)
        return "answer from pi", _canonical_ask_metadata()

    with (
        patch(
            "syke.daemon.ipc.daemon_runtime_status",
            return_value={"alive": True, "busy": False, "provider": "kimi-coding"},
        ),
        patch(
            "syke.daemon.ipc.ask_via_daemon",
            side_effect=RuntimeError("daemon busy: runtime in use"),
        ),
    ):
        _install_fake_module("syke.llm.backends.pi_ask", pi_ask=fake_pi_ask)
        answer_text, metadata = RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
                event_db_path=str(event_db_path),
            ),
            "user",
            "question",
        )

    assert answer_text == "answer from pi"
    assert metadata["backend"] == "pi"
    transport_details = cast(dict[str, object], captured["transport_details"])
    assert transport_details["ipc_fallback"] is True
    assert "daemon busy" in str(transport_details["ipc_error"])


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
