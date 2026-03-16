"""LLM-powered adapter generator — generates adapter code from format analysis."""

from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from syke.sense.analyzer import AnalysisResult
from syke.sense.sandbox import AdapterSandbox, SandboxResult

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def _strip_markdown_fencing(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


# One-line harness hints. The agent reads these + sample data to generate adapters.
# Each line: name | format | default paths | key fields | relationships.
# Paths are defaults — the agent should discover actual locations if they differ.
HARNESS_HINTS = {
    "claude-code": "JSONL at ~/.claude/projects/**/*.jsonl, ~/.claude/transcripts/*.jsonl. Lines: type(user|assistant|tool_use), sessionId, timestamp, message.content[].text. parentSessionId links sub-agents. agentSlug for agent type. message.model, message.usage for token counts.",
    "codex": "JSONL at ~/.codex/sessions/rollout-*.jsonl. First line: session_meta with cwd. Turns: response_item, payload.type=message, payload.role, payload.content[].type(input_text|output_text).text. Tools: payload.type=function_call with name+arguments, function_call_output with call_id+output.",
    "opencode": "SQLite at ~/.local/share/opencode/opencode.db. Tables: session(id, title, parent_id, time_created ms), message(session_id, data JSON with role+time.created), part(message_id, data JSON with type=text|tool). part.data.tool has name, callID, state.input/output/status.",
    "pi": "JSONL or JSON at ~/.pi/. Session files with role-based turns. Look for sessions/, conversations/, or history/ subdirs. Format varies by version — analyze samples to determine structure.",
    "hermes": "SQLite at ~/.hermes/state.db. Agent conversation state. Look for messages/turns tables with role, content, tool_calls. Also check ~/.hermes/sessions/ for JSONL conversation logs.",
    "cursor": "JSON at ~/.cursor/. Workspace state and conversation history. Look for conversations/ or history/ subdirs with session-based JSON files containing role+content turns.",
    "gemini": "JSON at ~/.gemini/. Google AI Studio conversation exports. Look for conversations/ with JSON files containing parts[].text, role(user|model), and tool usage.",
    "windsurf": "JSONL or JSON at ~/.windsurf/ or ~/.codeium/windsurf/. Similar structure to Cursor — workspace conversations with role-based turns and tool calls.",
    "aider": "JSONL at ~/.aider*/. Chat history files with role(user|assistant), content, and file edit blocks. Multiple aider versions may have different directory names.",
    "zed": "JSON at ~/.zed/ or ~/.config/zed/. Conversation history with assistant messages, tool use blocks, and file context references.",
}


@dataclass
class GeneratedAdapter:
    descriptor_toml: str
    adapter_code: str
    test_code: str
    sandbox_result: SandboxResult | None = None


class AdapterGenerator:
    def __init__(
        self,
        sandbox: AdapterSandbox | None = None,
        llm_fn: callable | None = None,
        max_retries: int = 3,
    ):
        self._sandbox = sandbox or AdapterSandbox()
        self._llm_fn = llm_fn  # callable(prompt) -> str; None = use template
        self._max_retries = max_retries

    def generate(
        self, analysis: AnalysisResult, samples: list[str], source_name: str = "unknown"
    ) -> GeneratedAdapter:
        descriptor = self._generate_descriptor(analysis, source_name)

        code = None
        test_code = None
        result = None

        for attempt in range(self._max_retries):
            if self._llm_fn:
                raw = self._llm_fn(self._build_prompt(analysis, samples, source_name))
                code = _strip_markdown_fencing(raw)
            else:
                code = self._generate_template(analysis, source_name)

            test_code = self._generate_test(source_name, samples)
            result = self._sandbox.test_adapter(code, samples)

            if result.success:
                return GeneratedAdapter(
                    descriptor_toml=descriptor,
                    adapter_code=code,
                    test_code=test_code,
                    sandbox_result=result,
                )
            logger.warning(
                "Sandbox rejected attempt %d/%d: %s",
                attempt + 1,
                self._max_retries,
                result.errors,
            )

        return GeneratedAdapter(
            descriptor_toml=descriptor,
            adapter_code=code,
            test_code=test_code,
            sandbox_result=result,
        )

    def _generate_descriptor(self, analysis: AnalysisResult, source_name: str) -> str:
        return textwrap.dedent(
            f'''
            [harness]
            name = "{source_name}"
            format = "{analysis.format}"
            
            [discover]
            roots = ["~/.{source_name}/"]
            glob = "**/*.{analysis.format}"
            
            [session]
            id_field = "session_id"
            timestamp_field = "{analysis.timestamp_field or "timestamp"}"
            
            [turn]
            role_field = "{analysis.role_field or "role"}"
            content_field = "{analysis.content_field or "content"}"
        '''
        ).strip()

    def _generate_template(self, analysis: AnalysisResult, source_name: str) -> str:
        ts = analysis.timestamp_field or "timestamp"
        role = analysis.role_field or "role"
        content = analysis.content_field or "content"

        return textwrap.dedent(
            f'''
            import json
            
            def parse_line(line):
                data = json.loads(line)
                return {{
                    "timestamp": data.get("{ts}"),
                    "role": data.get("{role}"),
                    "content": data.get("{content}"),
                }}
        '''
        ).strip()

    def _generate_test(self, source_name: str, samples: list[str]) -> str:
        sample_repr = repr(samples[:3])
        return textwrap.dedent(
            f"""
            import json
            from adapter import parse_line
            
            SAMPLES = {sample_repr}
            
            def test_parse_samples():
                for line in SAMPLES:
                    result = parse_line(line)
                    assert result is not None
                    assert "timestamp" in result or result.get("timestamp") is None
        """
        ).strip()

    def _build_prompt(self, analysis: AnalysisResult, samples: list[str], source_name: str) -> str:
        hint = HARNESS_HINTS.get(source_name, "")
        hint_block = f"\nKnown harness hint: {hint}" if hint else ""
        return textwrap.dedent(f"""\
            Generate a Python function `parse_line(line: str) -> dict | None` for the "{source_name}" harness.

            The function receives one raw line (string) from the harness data file and returns a dict with these canonical fields (use None for missing):
            - timestamp: ISO 8601 string
            - session_id: string grouping turns within one conversation
            - parent_session_id: string linking sub-agent sessions to parent (if available)
            - event_type: "turn" | "tool_call" | "tool_result" | "session.start"
            - role: "user" | "assistant" | "system"
            - content: the text content of the turn
            - tool_name: name of the tool if event_type is tool_call/tool_result
            - model: model name/id if available
            - input_tokens: int if available
            - output_tokens: int if available

            Return None for lines that should be skipped (meta lines, empty, unknown types).
            {hint_block}

            Schema analysis: format={analysis.format}, timestamp_field={analysis.timestamp_field}, role_field={analysis.role_field}, content_field={analysis.content_field}, tool_fields={analysis.tool_fields}

            Sample data (first 5 lines):
            {chr(10).join(samples[:5])}

            Rules:
            - Only use json standard library. No other imports.
            - Handle malformed lines gracefully (return None, never raise).
            - Extract as many canonical fields as the data supports.
            - Map harness-specific field names to canonical names above.""")
