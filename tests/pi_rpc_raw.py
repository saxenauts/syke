"""
Raw Pi RPC test — subprocess.Popen, sync JSON-lines protocol.
Tests 3 turns of conversation plus session stats.

Protocol learned from Pi source (dist/modes/rpc/rpc-mode.js) and live testing:
  - Commands: JSON objects with `type` field sent as newline-delimited JSON on stdin
  - Prompt command uses `message` field (NOT `content`)
  - No session header on startup — Pi just waits for commands
  - Events stream as AgentSessionEvent objects on stdout:
      * agent_start, turn_start, message_start, message_update, message_end, turn_end, agent_end
      * Text deltas are NESTED: message_update -> assistantMessageEvent.type="text_delta", .delta="..."
  - Responses have type="response", command, success, optional data/error
  - Must wait for agent_end before sending next prompt
"""

import json
import subprocess
import sys
import threading
import time


def send(proc, cmd):
    """Write a JSON command + newline to proc's stdin and flush."""
    line = json.dumps(cmd) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()


class StdoutCollector:
    """Thread-safe collector for stdout lines with a movable cursor."""

    def __init__(self, proc):
        self.lines = []
        self.lock = threading.Lock()
        self.cursor = 0  # global read position

        def reader():
            for line in proc.stdout:
                stripped = line.rstrip()
                if stripped:
                    with self.lock:
                        self.lines.append(stripped)

        self.thread = threading.Thread(target=reader, daemon=True)
        self.thread.start()

    def drain_to_agent_end(self, timeout=30):
        """
        Read from current cursor position, collect text deltas,
        return collected text when agent_end is seen.
        Advances cursor past agent_end.
        """
        collected = ""
        start = time.time()

        while time.time() - start < timeout:
            with self.lock:
                current_len = len(self.lines)

            if self.cursor >= current_len:
                time.sleep(0.05)
                continue

            with self.lock:
                snapshot = list(self.lines[self.cursor : current_len])

            for idx, raw in enumerate(snapshot):
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"  [non-json] {raw}")
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "message_update":
                    evt = msg.get("assistantMessageEvent", {})
                    evt_type = evt.get("type", "")
                    if evt_type == "text_delta":
                        delta = evt.get("delta", "")
                        collected += delta
                        print(f"  [text_delta] {delta!r}", end="", flush=True)
                    elif evt_type == "text_start":
                        pass  # stream start marker
                    elif evt_type == "text_end":
                        pass  # stream end marker
                    else:
                        print(f"  [message_update/{evt_type}]", flush=True)

                elif msg_type == "agent_end":
                    print()  # newline after deltas
                    usage = msg.get("usage", {})
                    print(f"  [agent_end] usage={usage}")
                    self.cursor = self.cursor + idx + 1
                    return collected

                elif msg_type == "response":
                    cmd = msg.get("command", "")
                    ok = msg.get("success", False)
                    err = msg.get("error", "")
                    if not ok:
                        print(f"  [response ERROR] command={cmd} error={err}")
                    else:
                        print(f"  [response OK] command={cmd}")

                elif msg_type in (
                    "agent_start",
                    "turn_start",
                    "turn_end",
                    "message_start",
                    "message_end",
                ):
                    pass  # lifecycle events, skip silently

                else:
                    print(f"  [{msg_type}] {json.dumps(msg, indent=None)[:150]}")

            self.cursor = self.cursor + len(snapshot)

        print(f"\n  [timeout after {timeout}s]")
        return collected

    def wait_for_response(self, command_name, timeout=10):
        """Wait for a response message matching a specific command name."""
        start = time.time()
        while time.time() - start < timeout:
            with self.lock:
                current_len = len(self.lines)

            for i in range(self.cursor, current_len):
                with self.lock:
                    raw = self.lines[i]
                try:
                    msg = json.loads(raw)
                    if msg.get("type") == "response" and msg.get("command") == command_name:
                        self.cursor = i + 1
                        return msg
                except json.JSONDecodeError:
                    continue

            time.sleep(0.1)
        return None


def main():
    t_start = time.time()
    passed = True

    pi_cmd = [
        "pi",
        "--mode",
        "rpc",
        "--no-session",
        "--no-tools",
        "--model",
        "azure-openai-responses/gpt-4.1-mini",
    ]

    print("=== Pi RPC Raw Test ===")
    print(f"Command: {' '.join(pi_cmd)}")
    print()

    proc = subprocess.Popen(
        pi_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    collector = StdoutCollector(proc)

    # Also drain stderr in background
    stderr_lines = []

    def stderr_reader():
        for line in proc.stderr:
            stderr_lines.append(line.rstrip())

    stderr_thread = threading.Thread(target=stderr_reader, daemon=True)
    stderr_thread.start()

    # Give Pi a moment to start up
    time.sleep(2)
    print(f"Startup: {len(collector.lines)} stdout lines, {len(stderr_lines)} stderr lines")
    if stderr_lines:
        for line in stderr_lines:
            print(f"  STDERR: {line}")
    print()

    # ── Turn 1: Simple math ──
    t1 = time.time()
    print("── Turn 1: What is 2+2? ──")
    send(
        proc,
        {
            "type": "prompt",
            "message": "What is 2+2? Reply with just the number.",
        },
    )
    text1 = collector.drain_to_agent_end(timeout=30)
    print(f"  Full response: {text1!r}")
    print(f"  Turn 1 time: {time.time() - t1:.2f}s")
    if "4" not in text1:
        print("  ✗ Expected '4' in response")
        passed = False
    else:
        print("  ✓ Got '4'")
    print()

    # ── Turn 2: Memory set ──
    t2 = time.time()
    print("── Turn 2: Set name ──")
    send(
        proc,
        {
            "type": "prompt",
            "message": "Remember: my name is TestUser. Just say noted.",
        },
    )
    text2 = collector.drain_to_agent_end(timeout=30)
    print(f"  Full response: {text2!r}")
    print(f"  Turn 2 time: {time.time() - t2:.2f}s")
    print()

    # ── Turn 3: Memory recall ──
    t3 = time.time()
    print("── Turn 3: Recall name ──")
    send(
        proc,
        {
            "type": "prompt",
            "message": "What is my name? One word.",
        },
    )
    text3 = collector.drain_to_agent_end(timeout=30)
    print(f"  Full response: {text3!r}")
    print(f"  Turn 3 time: {time.time() - t3:.2f}s")
    if "TestUser" in text3:
        print("  ✓ Memory recall: 'TestUser' found")
    else:
        print("  ✗ Memory recall: 'TestUser' NOT found in response")
        passed = False
    print()

    # ── Session stats ──
    print("── Session Stats ──")
    send(proc, {"type": "get_session_stats"})
    stats_msg = collector.wait_for_response("get_session_stats", timeout=10)

    if stats_msg and stats_msg.get("success"):
        stats = stats_msg.get("data", {})
        print(f"  Stats: {json.dumps(stats, indent=2)}")
        tokens = stats.get("tokens", "N/A")
        cost = stats.get("cost", "N/A")
        print(f"  Tokens: {tokens}")
        print(f"  Cost: {cost}")
    else:
        print(f"  [warning] could not get session stats: {stats_msg}")
    print()

    # ── Cleanup ──
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    t_total = time.time() - t_start

    print(f"=== Total time: {t_total:.2f}s ===")
    print()

    if stderr_lines:
        print("Stderr output:")
        for line in stderr_lines:
            print(f"  {line}")
        print()

    if passed:
        print("PASS ✓ — Pi RPC sync pattern works end-to-end")
    else:
        print("FAIL ✗ — See errors above")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
