"""Local translator proxy: Anthropic Messages API ↔ Codex Responses API.

Runs on localhost when Codex is the active provider. The Anthropic Agent SDK
sends requests to http://localhost:PORT/v1/messages, this proxy translates
them to the Codex backend format at chatgpt.com/backend-api/codex/responses,
and streams translated SSE events back.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

from syke.config_file import expand_path

log = logging.getLogger(__name__)

_CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
_DEFAULT_MODEL = "gpt-5.3-codex"


def _read_codex_model() -> str:
    """Read model from ~/.codex/config.toml, fall back to _DEFAULT_MODEL."""
    try:
        path = expand_path("~/.codex") / "config.toml"
        if not path.exists():
            return _DEFAULT_MODEL
        import tomllib

        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        model = cfg.get("model", "")
        if not model:
            return _DEFAULT_MODEL
        if "-codex" not in model:
            model = f"{model}-codex"
        return model
    except Exception:
        return _DEFAULT_MODEL


# ── Request Translation (Anthropic → Codex Responses) ─────────────────────


def translate_request(body: dict[str, Any]) -> dict[str, Any]:
    """Convert Anthropic Messages API request to Codex Responses API format.

    Preserves the original Anthropic model name in ``_original_model`` so the
    proxy can echo it back in SSE events (the Claude CLI validates that the
    response model matches what it requested).
    """
    codex_input: list[dict[str, Any]] = []

    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")

        if isinstance(content, str):
            if role == "user":
                codex_input.append(
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": content}],
                    }
                )
            elif role == "assistant":
                codex_input.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content}],
                    }
                )
            continue

        if not isinstance(content, list):
            continue

        # Complex content blocks
        text_parts: list[dict[str, Any]] = []
        for block in content:
            btype = block.get("type", "")

            if btype == "text":
                text_parts.append(block)

            elif btype == "tool_use":
                # Flush text first
                if text_parts and role == "assistant":
                    codex_input.append(
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": t["text"]} for t in text_parts
                            ],
                        }
                    )
                    text_parts = []
                codex_input.append(
                    {
                        "type": "function_call",
                        "name": block["name"],
                        "call_id": block.get("id", f"call_{uuid.uuid4().hex[:12]}"),
                        "arguments": json.dumps(block.get("input", {})),
                    }
                )

            elif btype == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_content = "\n".join(
                        b.get("text", "") for b in result_content if b.get("type") == "text"
                    )
                codex_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": block.get("tool_use_id", ""),
                        "output": str(result_content),
                    }
                )

        # Remaining text
        if text_parts:
            if role == "user":
                codex_input.append(
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": t["text"]} for t in text_parts],
                    }
                )
            elif role == "assistant":
                codex_input.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": t["text"]} for t in text_parts],
                    }
                )

    # System prompt → instructions
    system = body.get("system", "")
    if isinstance(system, list):
        system = "\n".join(b.get("text", "") for b in system if b.get("type") == "text")
    instructions = system or "You are a helpful assistant."

    # Tools
    tools: list[dict[str, Any]] = []
    for tool in body.get("tools", []):
        tools.append(
            {
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
                "strict": False,
            }
        )

    result: dict[str, Any] = {
        "model": _read_codex_model(),
        "instructions": instructions,
        "input": codex_input,
        "store": False,
        "stream": True,
        "reasoning": {"effort": "high", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
        "_original_model": body.get("model", "claude-sonnet-4-6"),
    }

    if tools:
        result["tools"] = tools
        result["tool_choice"] = body.get("tool_choice", "auto")
        if isinstance(result["tool_choice"], dict):
            result["tool_choice"] = "auto"

    return result


# ── Response Translation (Codex SSE → Anthropic SSE) ──────────────────────

_STOP_REASON_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "end_turn",
}


class AnthropicSSEBuilder:
    """Translates Codex Responses API SSE events into Anthropic Messages API SSE events."""

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        self.model = model
        self.msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        self.block_index = 0
        self.in_text_block = False
        self.in_tool_block = False
        self.tool_name = ""
        self.tool_id = ""
        self.output_tokens = 0

    def message_start(self) -> str:
        event = {
            "type": "message_start",
            "message": {
                "id": self.msg_id,
                "type": "message",
                "role": "assistant",
                "model": self.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
        return f"event: message_start\ndata: {json.dumps(event)}\n\n"

    def _start_text_block(self) -> str:
        self.in_text_block = True
        event = {
            "type": "content_block_start",
            "index": self.block_index,
            "content_block": {"type": "text", "text": ""},
        }
        return f"event: content_block_start\ndata: {json.dumps(event)}\n\n"

    def _stop_current_block(self) -> str:
        event = {"type": "content_block_stop", "index": self.block_index}
        self.block_index += 1
        self.in_text_block = False
        self.in_tool_block = False
        return f"event: content_block_stop\ndata: {json.dumps(event)}\n\n"

    def text_delta(self, text: str) -> str:
        out = ""
        if not self.in_text_block:
            out += self._start_text_block()
        self.output_tokens += max(1, len(text) // 4)  # rough estimate
        event = {
            "type": "content_block_delta",
            "index": self.block_index,
            "delta": {"type": "text_delta", "text": text},
        }
        out += f"event: content_block_delta\ndata: {json.dumps(event)}\n\n"
        return out

    def start_tool_use(self, call_id: str, name: str) -> str:
        out = ""
        if self.in_text_block:
            out += self._stop_current_block()
        self.in_tool_block = True
        self.tool_name = name
        self.tool_id = call_id
        event = {
            "type": "content_block_start",
            "index": self.block_index,
            "content_block": {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": {},
            },
        }
        out += f"event: content_block_start\ndata: {json.dumps(event)}\n\n"
        return out

    def tool_args_delta(self, partial: str) -> str:
        event = {
            "type": "content_block_delta",
            "index": self.block_index,
            "delta": {"type": "input_json_delta", "partial_json": partial},
        }
        return f"event: content_block_delta\ndata: {json.dumps(event)}\n\n"

    def message_end(self, stop_reason: str = "end_turn") -> str:
        out = ""
        if self.in_text_block or self.in_tool_block:
            out += self._stop_current_block()
        mapped = _STOP_REASON_MAP.get(stop_reason, stop_reason)
        event = {
            "type": "message_delta",
            "delta": {"stop_reason": mapped},
            "usage": {"output_tokens": self.output_tokens},
        }
        out += f"event: message_delta\ndata: {json.dumps(event)}\n\n"
        out += 'event: message_stop\ndata: {"type": "message_stop"}\n\n'
        return out

    def error_event(self, message: str) -> str:
        event = {
            "type": "error",
            "error": {"type": "api_error", "message": message},
        }
        return f"event: error\ndata: {json.dumps(event)}\n\n"


def translate_sse_event(event_type: str, data: dict[str, Any], builder: AnthropicSSEBuilder) -> str:
    """Translate a single Codex SSE event to Anthropic SSE event(s)."""

    if event_type == "response.output_text.delta":
        return builder.text_delta(data.get("delta", ""))

    if event_type == "response.output_text.done":
        return ""  # We already emitted deltas

    if event_type == "response.reasoning_summary_text.delta":
        return ""  # Drop reasoning summaries; Claude CLI doesn't expect them

    if event_type == "response.function_call_arguments.delta":
        return builder.tool_args_delta(data.get("delta", ""))

    if event_type == "response.output_item.added":
        item = data.get("item", {})
        if item.get("type") == "function_call":
            return builder.start_tool_use(
                call_id=item.get("call_id", f"call_{uuid.uuid4().hex[:12]}"),
                name=item.get("name", "unknown"),
            )
        return ""

    if event_type == "response.output_item.done":
        if builder.in_tool_block:
            return builder._stop_current_block()
        return ""

    if event_type == "response.completed":
        resp = data.get("response", {})
        status = resp.get("status", "completed")
        stop = "end_turn" if status == "completed" else status
        # Check if any output items have function calls
        for item in resp.get("output", []):
            if item.get("type") == "function_call":
                stop = "tool_use"
                break
        return builder.message_end(stop)

    if event_type == "response.failed":
        resp = data.get("response", {})
        err = resp.get("error", {})
        return builder.error_event(err.get("message", "Codex request failed"))

    if event_type == "error":
        return builder.error_event(data.get("message", "Unknown error"))

    return ""  # Ignore unknown events


# ── HTTP Proxy Server ─────────────────────────────────────────────────────


class _ProxyHandler(BaseHTTPRequestHandler):
    access_token: str = ""
    account_id: str = ""

    def do_GET(self) -> None:
        """Handle GET requests — the Claude CLI probes endpoints before /v1/messages."""
        path = self.path.rstrip("/")
        log.warning("codex-proxy GET %s", path)
        if path.endswith("/v1/models") or path.startswith("/v1/models"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = json.dumps(
                {
                    "data": [{"id": "claude-sonnet-4-6", "object": "model"}],
                    "object": "list",
                }
            )
            self.wfile.write(resp.encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if not path.endswith("/v1/messages"):
            self.send_error(404, "Only /v1/messages is supported")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length)

        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        codex_body = translate_request(body)
        original_model = codex_body.pop("_original_model", "claude-sonnet-4-6")
        upstream_model = codex_body.get("model", "unknown")

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
            "originator": "pi",
            "accept": "text/event-stream",
        }
        if self.account_id:
            headers["chatgpt-account-id"] = self.account_id

        log.info(
            "[PROXY] %s → %s (upstream_model=%s)",
            original_model,
            _CODEX_URL,
            upstream_model,
        )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        builder = AnthropicSSEBuilder(model=original_model)
        self.wfile.write(builder.message_start().encode())
        self.wfile.flush()

        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect=20, read=120, write=15, pool=30)
            ) as client:
                with client.stream("POST", _CODEX_URL, headers=headers, json=codex_body) as resp:
                    log.info("[PROXY] ← HTTP %d", resp.status_code)
                    if resp.status_code != 200:
                        error_text = ""
                        for chunk in resp.iter_text():
                            error_text += chunk
                        self.wfile.write(
                            builder.error_event(
                                f"Codex HTTP {resp.status_code}: {error_text[:200]}"
                            ).encode()
                        )
                        self.wfile.flush()
                        return

                    current_event = ""
                    for line in resp.iter_lines():
                        if line.startswith("event: "):
                            current_event = line[7:].strip()
                        elif line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
                            sse_out = translate_sse_event(current_event, data, builder)
                            if sse_out:
                                self.wfile.write(sse_out.encode())
                                self.wfile.flush()

            # Ensure message is properly closed
            if builder.in_text_block or builder.in_tool_block:
                self.wfile.write(builder.message_end("end_turn").encode())
                self.wfile.flush()

        except Exception as e:
            log.error("Codex proxy error: %s", e)
            self.wfile.write(builder.error_event(str(e)).encode())
            self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        log.debug(format, *args)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CodexProxy:
    """Manages the lifecycle of the local Codex translator proxy."""

    def __init__(self, access_token: str, account_id: str = "") -> None:
        self.access_token = access_token
        self.account_id = account_id
        self.port: int = 0
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> int:
        """Start the proxy server. Returns the port number."""
        self.port = _find_free_port()

        handler = type(
            "_BoundHandler",
            (_ProxyHandler,),
            {"access_token": self.access_token, "account_id": self.account_id},
        )

        self._server = HTTPServer(("127.0.0.1", self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("Codex proxy started on port %d", self.port)
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        log.info("Codex proxy stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# Module-level singleton for daemon/CLI use
_active_proxy: CodexProxy | None = None


def start_codex_proxy(access_token: str, account_id: str = "") -> int:
    global _active_proxy
    if _active_proxy and _active_proxy.is_running:
        return _active_proxy.port
    _active_proxy = CodexProxy(access_token, account_id)
    return _active_proxy.start()


def stop_codex_proxy() -> None:
    global _active_proxy
    if _active_proxy:
        _active_proxy.stop()
        _active_proxy = None


def get_codex_proxy_port() -> int | None:
    if _active_proxy and _active_proxy.is_running:
        return _active_proxy.port
    return None
