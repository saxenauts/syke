from __future__ import annotations

import sys

import pytest

from syke.daemon.ask_workers import DaemonAskCapacityExceeded, DaemonAskWorkerSupervisor


def test_daemon_ask_worker_supervisor_streams_events_and_result(tmp_path) -> None:
    script = """
import json
import os
import sys

request = json.loads(sys.stdin.read())
print(json.dumps({"type": "event", "event": {"type": "thinking", "content": "looking"}}), flush=True)
print(json.dumps({
    "type": "result",
    "answer": "worker answer",
    "metadata": {
        "backend": "pi",
        "transport": "daemon_worker",
        "worker_pid": os.getpid(),
        "question_seen": request["question"],
    },
}), flush=True)
"""
    seen: list[str] = []
    supervisor = DaemonAskWorkerSupervisor(
        max_workers=1,
        command=[sys.executable, "-c", script],
    )

    answer, metadata = supervisor.ask(
        user_id="test",
        syke_db_path=str(tmp_path / "syke.db"),
        question="what changed",
        on_event=lambda event: seen.append(f"{event.type}:{event.content}"),
        timeout=5.0,
        transport_details={"daemon_pid": 123, "routing_reason": "warm_runtime_busy"},
    )

    assert answer == "worker answer"
    assert seen == ["thinking:looking"]
    assert metadata["transport"] == "daemon_worker"
    assert metadata["question_seen"] == "what changed"
    assert metadata["routing_reason"] == "warm_runtime_busy"
    assert metadata["daemon_pid"] == 123
    assert isinstance(metadata["worker_pid"], int)
    assert isinstance(metadata["worker_roundtrip_ms"], int)
    assert isinstance(metadata["worker_slot_wait_ms"], int)


def test_daemon_ask_worker_supervisor_enforces_capacity() -> None:
    supervisor = DaemonAskWorkerSupervisor(max_workers=1, capacity_wait_s=0.0)
    assert supervisor._semaphore is not None
    assert supervisor._semaphore.acquire(blocking=False)

    try:
        with pytest.raises(DaemonAskCapacityExceeded, match="capacity exceeded"):
            supervisor.ask(
                user_id="test",
                syke_db_path="/tmp/syke.db",
                question="what changed",
                on_event=None,
                timeout=5.0,
                transport_details={},
            )
    finally:
        supervisor._semaphore.release()


def test_daemon_ask_worker_supervisor_times_out_child() -> None:
    script = "import time; time.sleep(30)"
    supervisor = DaemonAskWorkerSupervisor(
        max_workers=1,
        command=[sys.executable, "-c", script],
    )

    with pytest.raises(RuntimeError, match="timed out"):
        supervisor.ask(
            user_id="test",
            syke_db_path="/tmp/syke.db",
            question="what changed",
            on_event=None,
            timeout=0.1,
            transport_details={},
        )
