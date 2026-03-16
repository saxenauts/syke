"""LLM-powered adapter generator — generates adapter code from format analysis."""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass
from pathlib import Path

from syke.sense.analyzer import AnalysisResult
from syke.sense.sandbox import AdapterSandbox, SandboxResult

logger = logging.getLogger(__name__)


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
                code = self._llm_fn(self._build_prompt(analysis, samples, source_name))
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
        return f"Generate a parse_line function for {source_name} format. Analysis: {analysis}. Samples: {samples[:5]}"
