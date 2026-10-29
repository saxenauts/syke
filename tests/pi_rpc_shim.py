from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def respond(command: dict[str, Any], data: dict[str, Any] | None = None) -> None:
    emit(
        {
            "type": "response",
            "id": command.get("id"),
            "command": command.get("type"),
            "success": True,
            "data": data or {},
        }
    )


def emit_prompt_result(output: str, attempt: int, *, retry_once: bool = False) -> None:
    if retry_once:
        retry_error = {
            "role": "assistant",
            "provider": "shim",
            "model": "shim-model",
            "responseId": "shim-retryable",
            "stopReason": "error",
            "errorMessage": "429 rate limited",
            "content": [],
        }
        emit({"type": "agent_end", "willRetry": True, "messages": [retry_error]})
        time.sleep(0.05)
        emit(
            {
                "type": "auto_retry_start",
                "attempt": 1,
                "maxAttempts": 3,
                "delayMs": 50,
                "errorMessage": "429 rate limited",
            }
        )
        emit({"type": "auto_retry_end", "success": True, "attempt": 1})

    final_message = {
        "role": "assistant",
        "provider": "shim",
        "model": "shim-model",
        "responseId": f"shim-final-{attempt}",
        "stopReason": "stop",
        "content": [{"type": "text", "text": output}],
        "usage": {
            "input": 10,
            "output": 4,
            "cacheRead": 2,
            "cacheWrite": 0,
            "cost": {"total": 0.0},
        },
    }
    emit({"type": "message_end", "message": final_message})
    emit({"type": "agent_end", "willRetry": False, "messages": [final_message]})
    time.sleep(0.05)
    emit({"type": "agent_settled"})


def accepted_mutation(
    db_path: Path,
    workspace: Path,
    control_root: Path,
    user_id: str,
    cycle_id: str,
) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        changed = conn.execute(
            "UPDATE memories SET content = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (
                "revised by component",
                "2026-08-22T10:00:00+00:00",
                "memory-stable",
                user_id,
            ),
        )
        if changed.rowcount != 1:
            raise RuntimeError("accepted component could not revise the seeded memory")
        conn.execute(
            """INSERT INTO memories (id, user_id, content, created_at, updated_at)
               VALUES ('memory-new', ?, 'created by component',
                       '2026-08-22T10:00:00+00:00', NULL)""",
            (user_id,),
        )
        conn.execute(
            """INSERT INTO links (id, user_id, source_id, target_id, reason, created_at)
               VALUES ('link-new', ?, 'memory-stable', 'memory-new', 'continues',
                       '2026-08-22T10:00:00+00:00')""",
            (user_id,),
        )
        memex = conn.execute(
            """UPDATE current_memex SET content = ?, updated_at = ?
               WHERE singleton = 1 AND user_id = ?""",
            ("accepted component memex", "2026-08-22T10:00:00+00:00", user_id),
        )
        if memex.rowcount != 1:
            raise RuntimeError("accepted component could not revise the canonical MEMEX")
        conn.commit()

    artifact = workspace / "artifacts" / "component.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("accepted workspace effect\n", encoding="utf-8")


def repair_mutation(db_path: Path, user_id: str, prompt: str, attempt: int) -> str:
    if attempt == 1:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "UPDATE current_memex SET content = ? WHERE singleton = 1 AND user_id = ?",
                (" x" * 2_001, user_id),
            )
            conn.commit()
        return "rejected"

    if attempt != 2:
        raise RuntimeError("repair component received more than one repair prompt")
    if '"memex_tokens": 2001' not in prompt or '"memex_over_budget": true' not in prompt:
        raise RuntimeError("repair prompt omitted the exact MEMEX budget facts")

    with closing(sqlite3.connect(db_path)) as conn:
        restored = conn.execute(
            "SELECT content FROM current_memex WHERE singleton = 1 AND user_id = ?",
            (user_id,),
        ).fetchone()
        if restored != ("canonical memex",):
            raise RuntimeError(f"repair prompt observed un-restored state: {restored!r}")
        conn.execute(
            """UPDATE current_memex SET content = ?, updated_at = ?
               WHERE singleton = 1 AND user_id = ?""",
            (
                "repaired component memex",
                "2026-08-22T10:01:00+00:00",
                user_id,
            ),
        )
        conn.commit()
    return "repaired"


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit("usage: pi_rpc_shim.py SCENARIO DB WORKSPACE CONTROL_ROOT USER_ID")
    scenario = sys.argv[1]
    db_path = Path(sys.argv[2])
    workspace = Path(sys.argv[3])
    control_root = Path(sys.argv[4])
    user_id = sys.argv[5]
    session_name: str | None = None
    new_session_count = 0
    prompt_count = 0
    for line in sys.stdin:
        command = json.loads(line)
        command_type = command.get("type")
        if command_type == "new_session":
            new_session_count += 1
            if new_session_count != 1:
                raise RuntimeError("component started a second native session")
            respond(command)
        elif command_type == "set_session_name":
            session_name = command.get("name")
            respond(command)
        elif command_type == "get_state":
            respond(
                command,
                {
                    "sessionId": "shim-session",
                    "sessionFile": "/protected/shim-session.jsonl",
                    "sessionName": session_name,
                },
            )
        elif command_type == "get_session_stats":
            respond(command, {"assistantMessages": prompt_count})
        elif command_type == "prompt":
            if not isinstance(session_name, str) or not session_name.startswith("syke:synthesis:"):
                raise RuntimeError(f"invalid synthesis session name: {session_name!r}")
            prompt_count += 1
            cycle_id = session_name.removeprefix("syke:synthesis:")
            prompt = command.get("message")
            if not isinstance(prompt, str):
                raise RuntimeError("prompt message is missing")
            if scenario == "accepted":
                if prompt_count != 1:
                    raise RuntimeError("accepted component received an unexpected repair prompt")
                accepted_mutation(db_path, workspace, control_root, user_id, cycle_id)
                output = "accepted"
            elif scenario == "repair":
                output = repair_mutation(db_path, user_id, prompt, prompt_count)
            else:
                raise RuntimeError(f"unknown component scenario: {scenario}")
            emit_prompt_result(output, prompt_count, retry_once=scenario == "accepted")


if __name__ == "__main__":
    main()
