"""Simple prompt → string LLM callable for one-shot code generation."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from syke.config import DEFAULT_USER
from syke.runtime import start_pi_runtime
from syke.runtime.sandbox import write_sandbox_config
from syke.runtime.workspace import SESSIONS_DIR, WORKSPACE_ROOT, prepare_workspace

log = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BACKOFF_BASE = 5
_DEFAULT_TIMEOUT_SECONDS = 120.0
_HEARTBEAT_INTERVAL_SECONDS = 15.0


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


def _should_rebuild_runtime(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "pi runtime is not running" in message
        or "pi did not complete within" in message
        or "failed to send to pi" in message
        or "broken pipe" in message
    )


def build_llm_fn(
    model: str | None = None,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    extra_read_roots: list[Path] | None = None,
) -> Callable[[str], str]:
    """Build a prompt → string callable through the Pi runtime."""

    runtime = None

    def _ensure_runtime():
        nonlocal runtime
        if runtime is not None and runtime.is_alive:
            return runtime

        prepare_workspace(DEFAULT_USER)
        write_sandbox_config(WORKSPACE_ROOT, extra_read_roots=extra_read_roots)
        runtime = start_pi_runtime(
            workspace_dir=WORKSPACE_ROOT,
            session_dir=SESSIONS_DIR,
            model=model,
        )
        log.info("LLM callable: Pi runtime (model=%s)", runtime.model)
        return runtime

    def _drop_runtime(reason: Exception) -> None:
        nonlocal runtime
        if runtime is None:
            return
        log.warning("LLM callable dropping Pi runtime after error: %s", reason)
        with suppress(Exception):
            if runtime.is_alive:
                runtime.stop()
        runtime = None

    def call(prompt: str) -> str:
        def _do() -> str:
            active_runtime = _ensure_runtime()
            stop = threading.Event()
            started = time.monotonic()

            def _heartbeat() -> None:
                while not stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
                    elapsed = int(time.monotonic() - started)
                    log.info("LLM prompt still running (%ss elapsed)", elapsed)

            heartbeat = threading.Thread(target=_heartbeat, daemon=True)
            heartbeat.start()
            try:
                result = active_runtime.prompt(
                    prompt,
                    timeout=timeout_seconds,
                    new_session=True,
                )
            finally:
                stop.set()

            if not result.ok:
                raise RuntimeError(result.error or "Pi runtime call failed")
            output = result.output.strip()
            if not output:
                raise ValueError("Pi runtime returned no content")
            return output

        try:
            return _retry(_do)
        except Exception as exc:
            if _should_rebuild_runtime(exc):
                _drop_runtime(exc)
            raise

    return call
