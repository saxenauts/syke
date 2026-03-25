"""
PiClient integration tests — sync, context-managed, 4 test cases.

Uses PiClient from syke.llm.pi_client (not raw subprocess).
Model: azure-openai-responses/gpt-4.1-mini, thinking=off.
"""

import sys
import time

from syke.llm.pi_client import PiClient

MODEL = "azure-openai-responses/gpt-4.1-mini"


def test_basic_prompt(pi: PiClient) -> bool:
    """Test 1: basic prompt — send a simple question, assert output has content."""
    print("── Test 1: Basic Prompt ──")
    t0 = time.time()

    result = pi.prompt("What is 2+2? Reply with just the number.")
    output = result.get("output", "")
    usage = result.get("usage", {})

    print(f"  Output : {output!r}")
    print(f"  Usage  : {usage}")
    print(f"  Time   : {time.time() - t0:.2f}s")

    ok = bool(output and output.strip())
    print(f"  Result : {'PASS ✓' if ok else 'FAIL ✗ — empty output'}")
    print()
    return ok


def test_multi_turn(pi: PiClient) -> bool:
    """Test 2: multi-turn — set a name, then recall it."""
    print("── Test 2: Multi-Turn Memory ──")
    t0 = time.time()

    r1 = pi.prompt("My name is Bob. Say noted.")
    output1 = r1.get("output", "")
    print(f"  Turn 1 output: {output1!r}")

    r2 = pi.prompt("What is my name?")
    output2 = r2.get("output", "")
    print(f"  Turn 2 output: {output2!r}")
    print(f"  Time   : {time.time() - t0:.2f}s")

    ok = "Bob" in output2
    print(f"  Result : {'PASS ✓' if ok else 'FAIL ✗ — Bob not found in turn 2'}")
    print()
    return ok


def test_command_get_state(pi: PiClient) -> bool:
    """Test 3: command — send get_state, assert success=True."""
    print("── Test 3: Command (get_state) ──")
    t0 = time.time()

    # PiClient.command() takes the command type as a string, not a dict.
    # Internally it sends {"type": cmd} on stdin.
    resp = pi.command("get_state")

    print(f"  Response: {resp}")
    print(f"  Time    : {time.time() - t0:.2f}s")

    ok = resp.get("success", False) is True
    print(f"  Result  : {'PASS ✓' if ok else 'FAIL ✗ — success != True'}")
    print()
    return ok


def test_session_stats(pi: PiClient) -> bool:
    """Test 4: session stats — get_session_stats after prompts, print tokens/cost."""
    print("── Test 4: Session Stats ──")
    t0 = time.time()

    # Ensure at least one prompt has been sent so stats are populated.
    pi.prompt("Say hello.")

    resp = pi.command("get_session_stats")

    print(f"  Response: {resp}")

    if resp.get("success"):
        data = resp.get("data", {})
        tokens = data.get("tokens", "N/A")
        cost = data.get("cost", "N/A")
        print(f"  Tokens : {tokens}")
        print(f"  Cost   : {cost}")
    else:
        print(f"  Warning: could not get session stats — {resp.get('error', 'unknown')}")

    print(f"  Time   : {time.time() - t0:.2f}s")

    ok = resp.get("success", False) is True
    print(f"  Result : {'PASS ✓' if ok else 'FAIL ✗ — success != True'}")
    print()
    return ok


def main() -> None:
    t_start = time.time()
    results: dict[str, bool] = {}

    print("=== PiClient Integration Tests ===")
    print(f"Model: {MODEL}")
    print()

    with PiClient(model=MODEL, thinking="off") as pi:
        results["basic_prompt"] = test_basic_prompt(pi)
        results["multi_turn"] = test_multi_turn(pi)
        results["command_get_state"] = test_command_get_state(pi)
        results["session_stats"] = test_session_stats(pi)

    # ── Summary ──
    t_total = time.time() - t_start
    print("=== Summary ===")
    for name, passed in results.items():
        status = "PASS ✓" if passed else "FAIL ✗"
        print(f"  {name:25s} {status}")

    all_passed = all(results.values())
    print()
    print(f"Total time: {t_total:.2f}s")
    print(f"Overall: {'PASS ✓' if all_passed else 'FAIL ✗'}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
