"""Simple prompt → string LLM callable for one-shot code generation."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from syke.config import DEFAULT_USER
from syke.runtime import start_pi_runtime
from syke.runtime.workspace import SESSIONS_DIR, WORKSPACE_ROOT, prepare_workspace

log = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BACKOFF_BASE = 5
_DEFAULT_TIMEOUT_SECONDS = 120.0


def _retry(fn: Callable[[], str]) -> str:
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except Exception as exc:
            exc_str = str(exc).lower()
            if "rate" not in exc_str and "429" not in exc_str and "503" not in exc_str:
                raise
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = _BACKOFF_BASE * (2**attempt)
            log.warning(
                "Rate limited (attempt %d/%d), retrying in %ds", attempt + 1, _MAX_RETRIES, wait
            )
            time.sleep(wait)
    raise RuntimeError("Unreachable")


def build_llm_fn(model: str | None = None) -> Callable[[str], str]:
    """Build a prompt → string callable through the Pi runtime."""

    prepare_workspace(DEFAULT_USER)
    runtime = start_pi_runtime(
        workspace_dir=WORKSPACE_ROOT,
        session_dir=SESSIONS_DIR,
        model=model,
    )

    log.info("LLM callable: Pi runtime (model=%s)", runtime.model)

    def call(prompt: str) -> str:
        def _do() -> str:
            result = runtime.prompt(
                prompt,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
                new_session=True,
            )
            if not result.ok:
                raise RuntimeError(result.error or "Pi runtime call failed")
            output = result.output.strip()
            if not output:
                raise ValueError("Pi runtime returned no content")
            return output

        return _retry(_do)

    return call
