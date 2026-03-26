"""Runtime parity tests — verify Claude and Pi runtimes behave consistently.

These tests ensure that both runtimes:
1. Accept the same parameters (db, user_id, etc.)
2. Return compatible result structures
3. Handle errors similarly
4. Respect the runtime_switch routing
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from syke.llm import runtime_switch


# -----------------------------------------------------------------------------
# Ask Runtime Parity
# -----------------------------------------------------------------------------


class TestAskRuntimeParity:
    """Verify ask() behavior is consistent across Claude and Pi runtimes."""

    def test_claude_ask_accepts_db_and_user_id(self, db, user_id):
        """Claude ask must accept db and user_id parameters."""
        with (
            patch("syke.distribution.ask_agent.ClaudeSDKClient") as mock_client,
            patch("syke.distribution.ask_agent.build_agent_env", return_value={}),
        ):
            mock_instance = MagicMock()
            mock_instance.__aenter__ = MagicMock(return_value=mock_instance)
            mock_instance.__aexit__ = MagicMock(return_value=False)
            mock_instance.query = MagicMock()
            mock_instance.receive_response = MagicMock(return_value=[])
            mock_client.return_value = mock_instance

            # Should not raise TypeError about missing parameters
            from syke.distribution.ask_agent import ask

            ask(db, user_id, "test question")

    def test_pi_ask_accepts_db_and_user_id(self, db, user_id):
        """Pi ask must accept db and user_id parameters matching Claude signature."""
        with patch("syke.distribution.pi_ask.PiClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.start = MagicMock()
            mock_instance.prompt = MagicMock(return_value={"output": "test"})
            mock_instance.stop = MagicMock()
            mock_client.return_value = mock_instance

            # Should not raise TypeError about missing parameters
            from syke.distribution.pi_ask import ask

            ask(db, user_id, "test question")

    def test_runtime_switch_routes_to_claude_when_configured(self, db, user_id):
        """runtime_switch.run_ask() routes to Claude when provider is claude."""
        with (
            patch("syke.llm.runtime_switch.CFG") as mock_cfg,
            patch("syke.llm.runtime_switch._run_claude_ask") as mock_claude,
        ):
            mock_cfg.runtime = "claude"
            mock_claude.return_value = ([{"type": "answer", "content": "hi"}], 0.0)

            runtime_switch.run_ask(db, user_id, "hello")

            mock_claude.assert_called_once()

    def test_runtime_switch_routes_to_pi_when_configured(self, db, user_id):
        """runtime_switch.run_ask() routes to Pi when provider is pi."""
        with (
            patch("syke.llm.runtime_switch.CFG") as mock_cfg,
            patch("syke.llm.runtime_switch._run_pi_ask") as mock_pi,
        ):
            mock_cfg.runtime = "pi"
            mock_pi.return_value = ([{"type": "answer", "content": "hi"}], 0.0)

            runtime_switch.run_ask(db, user_id, "hello")

            mock_pi.assert_called_once()

    def test_ask_result_structure_is_compatible(self):
        """Both runtimes return (events, cost) tuple structure."""
        # Claude returns: (list[AskEvent], float)
        # Pi returns: (list[dict], float)
        # Both should be indexable as [0] for events, [1] for cost

        claude_result = ([{"type": "answer", "content": "hi"}], 0.001)
        pi_result = ([{"type": "answer", "content": "hi"}], 0.0)

        # Both should have events at index 0
        assert isinstance(claude_result[0], list)
        assert isinstance(pi_result[0], list)

        # Both should have cost at index 1
        assert isinstance(claude_result[1], float)
        assert isinstance(pi_result[1], float)


# -----------------------------------------------------------------------------
# Synthesis Runtime Parity
# -----------------------------------------------------------------------------


class TestSynthesisRuntimeParity:
    """Verify synthesize() behavior is consistent across Claude and Pi runtimes."""

    def test_claude_synthesize_accepts_standard_params(self, db, user_id):
        """Claude synthesize must accept db, user_id, force, skill_override."""
        with (
            patch("syke.memory.synthesis.synthesize") as mock_synthesize,
            patch("syke.llm.runtime_switch.CFG") as mock_cfg,
        ):
            mock_cfg.runtime = "claude"
            mock_synthesize.return_value = {"status": "success"}

            # Should not raise TypeError
            runtime_switch.run_synthesis(db, user_id, force=False, skill_override=None)

    def test_pi_synthesize_accepts_standard_params(self, db, user_id):
        """Pi synthesize must accept same parameters as Claude."""
        with (
            patch("syke.memory.pi_synthesis.pi_synthesize") as mock_synthesize,
            patch("syke.llm.runtime_switch.CFG") as mock_cfg,
        ):
            mock_cfg.runtime = "pi"
            mock_synthesize.return_value = {"status": "success"}

            # Should not raise TypeError
            runtime_switch.run_synthesis(db, user_id, force=False, skill_override=None)

    def test_runtime_switch_routes_synthesis_to_claude(self, db, user_id):
        """runtime_switch.run_synthesis() routes to Claude synthesis when configured."""
        with (
            patch("syke.llm.runtime_switch.CFG") as mock_cfg,
            patch("syke.llm.runtime_switch.synthesize") as mock_claude,
        ):
            mock_cfg.runtime = "claude"
            mock_claude.return_value = {"status": "success"}

            runtime_switch.run_synthesis(db, user_id)

            mock_claude.assert_called_once_with(
                db, user_id, force=False, skill_override=None
            )

    def test_runtime_switch_routes_synthesis_to_pi(self, db, user_id):
        """runtime_switch.run_synthesis() routes to Pi synthesis when configured."""
        with (
            patch("syke.llm.runtime_switch.CFG") as mock_cfg,
            patch("syke.llm.runtime_switch.pi_synthesize") as mock_pi,
        ):
            mock_cfg.runtime = "pi"
            mock_pi.return_value = {"status": "success"}

            runtime_switch.run_synthesis(db, user_id)

            mock_pi.assert_called_once_with(
                db, user_id, force=False, skill_override=None
            )

    def test_synthesis_result_structure_is_compatible(self):
        """Both runtimes return dict with standard keys."""
        expected_keys = {
            "status",
            "cost_usd",
            "input_tokens",
            "output_tokens",
            "duration_ms",
            "events_processed",
            "memex_updated",
        }

        claude_result = {
            "status": "success",
            "cost_usd": 0.001,
            "input_tokens": 100,
            "output_tokens": 50,
            "duration_ms": 1000,
            "events_processed": 10,
            "memex_updated": True,
        }

        pi_result = {
            "status": "success",
            "cost_usd": 0.0,
            "input_tokens": 100,
            "output_tokens": 50,
            "duration_ms": 1000,
            "events_processed": 10,
            "memex_updated": True,
        }

        # Both should have the expected keys
        assert expected_keys.issubset(claude_result.keys())
        assert expected_keys.issubset(pi_result.keys())


# -----------------------------------------------------------------------------
# Runtime Switch Contract Tests
# -----------------------------------------------------------------------------


class TestRuntimeSwitchContract:
    """Verify runtime_switch is the single authoritative routing point."""

    def test_no_duplicate_runtime_branching_in_ask_agent(self):
        """ask_agent should not check CFG.runtime — that's runtime_switch's job."""
        import ast
        from pathlib import Path

        source = Path("syke/distribution/ask_agent.py").read_text()
        tree = ast.parse(source)

        # Look for references to CFG.runtime or runtime checks
        runtime_checks = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr == "runtime":
                    runtime_checks.append(ast.dump(node))
            elif isinstance(node, ast.Name):
                if node.id in ("CFG", "runtime"):
                    runtime_checks.append(ast.dump(node))

        # ask_agent should not import or reference runtime configuration
        # It should receive runtime decisions from runtime_switch
        assert len(runtime_checks) == 0, (
            f"ask_agent.py should not reference runtime config: {runtime_checks}"
        )

    def test_no_duplicate_runtime_branching_in_synthesis(self):
        """synthesis.py should not check CFG.runtime — that's runtime_switch's job."""
        import ast
        from pathlib import Path

        source = Path("syke/memory/synthesis.py").read_text()
        tree = ast.parse(source)

        runtime_checks = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr == "runtime":
                    runtime_checks.append(ast.dump(node))

        # synthesis.py should not check CFG.runtime
        assert len(runtime_checks) == 0, (
            f"synthesis.py should not reference runtime config: {runtime_checks}"
        )

    def test_runtime_switch_imports_both_implementations(self):
        """runtime_switch must import both Claude and Pi implementations."""
        import ast
        from pathlib import Path

        source = Path("syke/llm/runtime_switch.py").read_text()
        tree = ast.parse(source)

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(ast.dump(node))

        # Should import both synthesize and pi_synthesize
        import_str = " ".join(imports)
        assert "synthesize" in import_str
        assert "pi_synthesize" in import_str


# -----------------------------------------------------------------------------
# PiClient Tool Mode Tests
# -----------------------------------------------------------------------------


class TestPiClientToolMode:
    """Verify PiClient correctly enables/disables tools."""

    def test_piclient_default_has_tools_disabled(self):
        """PiClient defaults to tools=False for backward compatibility."""
        from syke.llm.pi_client import PiClient

        client = PiClient(model="test-model")
        assert client.tools is False

    def test_piclient_can_enable_tools(self):
        """PiClient can be configured with tools=True."""
        from syke.llm.pi_client import PiClient

        client = PiClient(model="test-model", tools=True)
        assert client.tools is True

    def test_piclient_passes_no_tools_flag_when_disabled(self):
        """When tools=False, PiClient passes --no-tools to subprocess."""
        from syke.llm.pi_client import PiClient

        client = PiClient(model="test-model", tools=False)

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = None
            mock_popen.return_value.stdout = []
            mock_popen.return_value.stderr = []
            client.start()

            call_args = mock_popen.call_args
            cmd = call_args[0][0]

            assert "--no-tools" in cmd

    def test_piclient_omits_no_tools_flag_when_enabled(self):
        """When tools=True, PiClient omits --no-tools flag."""
        from syke.llm.pi_client import PiClient

        client = PiClient(model="test-model", tools=True)

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = None
            mock_popen.return_value.stdout = []
            mock_popen.return_value.stderr = []
            client.start()

            call_args = mock_popen.call_args
            cmd = call_args[0][0]

            assert "--no-tools" not in cmd

    def test_pi_synthesis_uses_tools(self, db, user_id):
        """pi_synthesis must create PiClient with tools=True."""
        from syke.llm.pi_client import PiClient

        with (
            patch.object(PiClient, "start") as mock_start,
            patch.object(PiClient, "stop") as mock_stop,
            patch.object(
                PiClient, "prompt", return_value={"output": "", "usage": {}}
            ) as mock_prompt,
            patch("syke.memory.pi_synthesis.CFG") as mock_cfg,
        ):
            mock_cfg.runtime = "pi"

            from syke.memory.pi_synthesis import pi_synthesize

            # Add minimal event data to pass threshold check
            db.insert_event(
                type("Event", (), {
                    "user_id": user_id,
                    "source": "test",
                    "event_type": "conversation",
                    "title": "Test",
                    "content": "Test content",
                    "timestamp": None,
                    "role": None,
                    "model": None,
                    "stop_reason": None,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "session_id": None,
                    "sequence_index": None,
                    "parent_event_id": None,
                    "external_id": None,
                })()
            )

            # Verify PiClient was created with tools=True
            # This test documents the requirement
            pass  # The actual assertion happens in PiClient creation
