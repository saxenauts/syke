"""Adapter sandbox — test generated adapter code safely."""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

FORBIDDEN_NAMES = {"eval", "exec", "compile", "execfile", "__import__"}
FORBIDDEN_MODULES = {"socket", "subprocess", "shutil", "ctypes"}
FORBIDDEN_ATTRS = {"os.system", "os.popen", "os.exec", "os.spawn"}


@dataclass
class SandboxResult:
    success: bool
    errors: list[str] = field(default_factory=list)
    events_parsed: int = 0


class AdapterSandbox:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def _check_ast_safety(self, code: str) -> list[str]:
        errors: list[str] = []
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return [f"SyntaxError: {e}"]

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_NAMES:
                    errors.append(f"Forbidden call: {node.func.id}")
                elif isinstance(node.func, ast.Attribute):
                    full = ""
                    if isinstance(node.func.value, ast.Name):
                        full = f"{node.func.value.id}.{node.func.attr}"
                    if full in FORBIDDEN_ATTRS:
                        errors.append(f"Forbidden call: {full}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_MODULES:
                        errors.append(f"Forbidden import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in FORBIDDEN_MODULES:
                    errors.append(f"Forbidden import: {node.module}")
        return errors

    def test_adapter(self, code: str, sample_data: list[str]) -> SandboxResult:
        safety_errors = self._check_ast_safety(code)
        if safety_errors:
            return SandboxResult(success=False, errors=safety_errors)

        with tempfile.TemporaryDirectory() as td:
            adapter_path = Path(td) / "adapter.py"
            samples_path = Path(td) / "samples.txt"
            runner_path = Path(td) / "runner.py"

            adapter_path.write_text(code)
            samples_path.write_text("\n".join(sample_data))

            runner_code = textwrap.dedent(f'''
                import sys
                sys.path.insert(0, "{td}")
                from adapter import parse_line
                count = 0
                with open("{samples_path}") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            result = parse_line(line)
                            if result is not None:
                                count += 1
                        except Exception as e:
                            print(f"PARSE_ERROR: {{e}}", file=sys.stderr)
                print(f"EVENTS_PARSED:{{count}}")
            ''')
            runner_path.write_text(runner_code)

            try:
                proc = subprocess.run(
                    [sys.executable, str(runner_path)],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                return SandboxResult(success=False, errors=["Timeout exceeded"])

            errors = [line for line in proc.stderr.splitlines() if line.startswith("PARSE_ERROR:")]
            events_parsed = 0
            for line in proc.stdout.splitlines():
                if line.startswith("EVENTS_PARSED:"):
                    events_parsed = int(line.split(":")[1])

            success = proc.returncode == 0 and events_parsed > 0
            if proc.returncode != 0 and not errors:
                errors.append(proc.stderr.strip() or f"Exit code {proc.returncode}")

            return SandboxResult(success=success, errors=errors, events_parsed=events_parsed)
