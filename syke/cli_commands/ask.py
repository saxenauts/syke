"""Ask command for the Syke CLI."""

from __future__ import annotations

import json
import logging as _logging
import signal as _signal
import sys as _sys

import click
from rich.console import Console

from syke.cli_support.ask_output import JsonlAskEventCoalescer, build_ask_result_payload
from syke.cli_support.context import get_db
from syke.cli_support.exit_codes import provider_resolution_exit_code
from syke.llm.backends import AskEvent

console = Console()


@click.command(short_help="Ask a grounded question over your local memory.")
@click.argument("question")
@click.option("--json", "use_json", is_flag=True, help="Output final result as JSON")
@click.option(
    "--jsonl",
    "use_jsonl",
    is_flag=True,
    help="Stream events and the final result as JSONL",
)
@click.pass_context
def ask(ctx: click.Context, question: str, use_json: bool, use_jsonl: bool) -> None:
    """Ask a grounded question over the local Syke store."""
    from syke.llm.env import resolve_provider
    from syke.llm.pi_runtime import run_ask

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        if use_json and use_jsonl:
            raise click.UsageError("--json and --jsonl are mutually exclusive.")

        try:
            provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
            provider_label = provider.id
        except Exception as exc:
            exit_code = provider_resolution_exit_code(exc)
            provider_label = "unknown"
            if use_json or use_jsonl:
                payload = build_ask_result_payload(
                    question=question,
                    answer=None,
                    provider=provider_label,
                    metadata=None,
                    ok=False,
                    error=str(exc),
                )
                if use_jsonl:
                    _sys.stdout.write(
                        json.dumps(
                            {"type": "status", "phase": "starting", "provider": provider_label}
                        )
                        + "\n"
                    )
                    _sys.stdout.write(
                        json.dumps({"type": "error", "error": str(exc), "provider": provider_label})
                        + "\n"
                    )
                else:
                    _sys.stdout.write(json.dumps(payload) + "\n")
                _sys.stdout.flush()
                raise SystemExit(exit_code) from exc
            _sys.stderr.write(f"Ask failed ({provider_label}): {exc}\n")
            _sys.stderr.flush()
            raise SystemExit(exit_code) from exc

        _sigterm_fired = False

        def _on_sigterm(signum, frame):
            nonlocal _sigterm_fired
            _sigterm_fired = True
            raise SystemExit(143)

        prev_handler = _signal.signal(_signal.SIGTERM, _on_sigterm)

        syke_logger = _logging.getLogger("syke")
        saved_levels = {
            h: h.level
            for h in syke_logger.handlers
            if isinstance(h, _logging.StreamHandler) and not isinstance(h, _logging.FileHandler)
        }
        for h in saved_levels:
            h.setLevel(_logging.CRITICAL)

        has_thinking = False
        has_streamed_text = False

        def _emit_json_line(payload: dict[str, object]) -> None:
            _sys.stdout.write(json.dumps(payload) + "\n")
            _sys.stdout.flush()

        jsonl_coalescer = JsonlAskEventCoalescer(_emit_json_line) if use_jsonl else None
        if use_jsonl:
            _emit_json_line({"type": "status", "phase": "starting", "provider": provider_label})

        def _on_event(event: AskEvent) -> None:
            nonlocal has_thinking, has_streamed_text
            try:
                if use_jsonl:
                    if jsonl_coalescer is not None:
                        jsonl_coalescer.push(event)
                    return
                if use_json:
                    return
                if event.type == "thinking":
                    if not has_thinking:
                        _sys.stderr.write("\033[2;3m")
                        has_thinking = True
                    _sys.stderr.write(event.content)
                    _sys.stderr.flush()
                elif event.type == "text":
                    if has_thinking:
                        _sys.stderr.write("\033[0m\n")
                        _sys.stderr.flush()
                        has_thinking = False
                    has_streamed_text = True
                    _sys.stdout.write(event.content)
                    _sys.stdout.flush()
                elif event.type == "tool_call":
                    if has_thinking:
                        _sys.stderr.write("\033[0m\n")
                        _sys.stderr.flush()
                        has_thinking = False
                    preview = ""
                    inp = event.metadata and event.metadata.get("input")
                    if isinstance(inp, dict):
                        for v in inp.values():
                            if isinstance(v, str) and v:
                                preview = v[:60]
                                break
                    tool_name = event.content.removeprefix("mcp__syke__")
                    label = f"  ↳ {tool_name}({preview})"
                    _sys.stderr.write(f"\033[2m{label}\033[0m\n")
                    _sys.stderr.flush()
            except BrokenPipeError:
                return  # pipe closed — stop writing, don't crash the runtime

        try:
            answer, cost = run_ask(
                db=db,
                user_id=user_id,
                question=question,
                on_event=_on_event,
            )
        except BrokenPipeError:
            raise SystemExit(0) from None
        except Exception as e:
            if has_thinking and not (use_json or use_jsonl):
                _sys.stderr.write("\033[0m\n")
                _sys.stderr.flush()
            if jsonl_coalescer is not None:
                jsonl_coalescer.flush()
            for h, lvl in saved_levels.items():
                h.setLevel(lvl)
            if use_json or use_jsonl:
                payload = build_ask_result_payload(
                    question=question,
                    answer=None,
                    provider=provider_label,
                    metadata=None,
                    ok=False,
                    error=str(e),
                )
                if use_jsonl:
                    _emit_json_line({"type": "error", "error": str(e), "provider": provider_label})
                else:
                    _sys.stdout.write(json.dumps(payload) + "\n")
                    _sys.stdout.flush()
                raise SystemExit(1) from e
            _sys.stderr.write(f"\nAsk failed ({provider_label}): {e}\n")
            _sys.stderr.flush()
            raise SystemExit(1) from e
        finally:
            if has_thinking and not (use_json or use_jsonl):
                _sys.stderr.write("\033[0m\n")
                _sys.stderr.flush()
            for h, lvl in saved_levels.items():
                h.setLevel(lvl)

        provider_out = provider_label
        if (
            isinstance(cost, dict)
            and isinstance(cost.get("provider"), str)
            and cost.get("provider")
        ):
            provider_out = cost["provider"]
        backend_error: str | None = None
        if isinstance(cost, dict):
            raw_error = cost.get("error")
            if isinstance(raw_error, str) and raw_error.strip():
                backend_error = raw_error.strip()

        if backend_error is not None:
            if has_streamed_text and not (use_json or use_jsonl):
                _sys.stdout.write("\n")
                _sys.stdout.flush()
            if use_json or use_jsonl:
                payload = build_ask_result_payload(
                    question=question,
                    answer=None,
                    provider=provider_out,
                    metadata=cost,
                    ok=False,
                    error=backend_error,
                )
                if use_jsonl:
                    if jsonl_coalescer is not None:
                        jsonl_coalescer.flush()
                    _emit_json_line(
                        {"type": "error", "error": backend_error, "provider": provider_out}
                    )
                else:
                    _sys.stdout.write(json.dumps(payload) + "\n")
                    _sys.stdout.flush()
                raise SystemExit(1)
            _sys.stderr.write(f"\nAsk failed ({provider_out}): {backend_error}\n")
            _sys.stderr.flush()
            raise SystemExit(1)

        result_payload = build_ask_result_payload(
            question=question,
            answer=answer,
            provider=provider_out,
            metadata=cost if isinstance(cost, dict) else None,
            ok=True,
            error=None,
        )

        if use_json:
            _sys.stdout.write(json.dumps(result_payload) + "\n")
            _sys.stdout.flush()
            return
        if use_jsonl:
            if jsonl_coalescer is not None:
                jsonl_coalescer.flush()
            _emit_json_line({"type": "result", **result_payload})
            return

        if not has_streamed_text and answer and answer.strip():
            _sys.stdout.write(f"\n{answer}\n")
            _sys.stdout.flush()
        elif has_streamed_text:
            _sys.stdout.write("\n")
            _sys.stdout.flush()

        if cost:
            duration_ms = cost.get("duration_ms")
            secs = float(duration_ms) / 1000 if isinstance(duration_ms, (int, float)) else 0.0
            usd_raw = cost.get("cost_usd")
            usd = float(usd_raw) if isinstance(usd_raw, (int, float)) else 0.0
            input_tokens = cost.get("input_tokens")
            output_tokens = cost.get("output_tokens")
            total_tokens = sum(
                token_count
                for token_count in (input_tokens, output_tokens)
                if isinstance(token_count, int)
            )
            tool_calls = cost.get("tool_calls")
            footer = f"\033[2m{provider_label} · {secs:.1f}s · ${usd:.4f} · {total_tokens} tokens"
            if isinstance(tool_calls, int):
                footer += f" · {tool_calls} tools"
            _sys.stderr.write(f"{footer}\033[0m\n")
    finally:
        prev_handler = locals().get("prev_handler")
        if prev_handler is not None:
            _signal.signal(_signal.SIGTERM, prev_handler)
        db.close()
