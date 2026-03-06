"""Tests for syke.llm.codex_proxy — request/response translation, SSE builder."""

from __future__ import annotations

import json

import pytest

from syke.llm.codex_proxy import (
    AnthropicSSEBuilder,
    translate_request,
    translate_sse_event,
)


# ── translate_request ─────────────────────────────────────────────────────


class TestTranslateRequest:
    def test_simple_user_message(self):
        body = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        result = translate_request(body)
        assert result["instructions"] == "You are helpful."
        assert result["store"] is False
        assert result["stream"] is True
        assert result["reasoning"]["effort"] == "high"
        assert "reasoning.encrypted_content" in result["include"]
        assert "max_tokens" not in result
        assert "temperature" not in result

        assert len(result["input"]) == 1
        inp = result["input"][0]
        assert inp["role"] == "user"
        assert inp["content"][0]["type"] == "input_text"
        assert inp["content"][0]["text"] == "Hello"

    def test_multi_turn_conversation(self):
        body = {
            "messages": [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "How are you?"},
            ],
        }
        result = translate_request(body)
        assert len(result["input"]) == 3
        assert result["input"][0]["role"] == "user"
        assert result["input"][1]["role"] == "assistant"
        assert result["input"][1]["content"][0]["type"] == "output_text"
        assert result["input"][2]["role"] == "user"

    def test_tool_use_blocks(self):
        body = {
            "messages": [
                {"role": "user", "content": "Search for X"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I'll search for that."},
                        {
                            "type": "tool_use",
                            "id": "call_123",
                            "name": "search",
                            "input": {"query": "X"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_123",
                            "content": "Found X results",
                        }
                    ],
                },
            ],
        }
        result = translate_request(body)

        # assistant text + function_call + function_call_output + nothing else
        fn_calls = [i for i in result["input"] if i.get("type") == "function_call"]
        fn_outputs = [
            i for i in result["input"] if i.get("type") == "function_call_output"
        ]
        assert len(fn_calls) == 1
        assert fn_calls[0]["name"] == "search"
        assert fn_calls[0]["call_id"] == "call_123"
        assert json.loads(fn_calls[0]["arguments"]) == {"query": "X"}
        assert len(fn_outputs) == 1
        assert fn_outputs[0]["call_id"] == "call_123"
        assert fn_outputs[0]["output"] == "Found X results"

    def test_tool_result_with_list_content(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_456",
                            "content": [
                                {"type": "text", "text": "line 1"},
                                {"type": "text", "text": "line 2"},
                            ],
                        }
                    ],
                }
            ],
        }
        result = translate_request(body)
        fn_outputs = [
            i for i in result["input"] if i.get("type") == "function_call_output"
        ]
        assert len(fn_outputs) == 1
        assert fn_outputs[0]["output"] == "line 1\nline 2"

    def test_system_as_list(self):
        body = {
            "system": [
                {"type": "text", "text": "System part 1."},
                {"type": "text", "text": "System part 2."},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
        }
        result = translate_request(body)
        assert result["instructions"] == "System part 1.\nSystem part 2."

    def test_tools_converted(self):
        body = {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [
                {
                    "name": "search",
                    "description": "Search for stuff",
                    "input_schema": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                }
            ],
        }
        result = translate_request(body)
        assert len(result["tools"]) == 1
        assert result["tools"][0]["type"] == "function"
        assert result["tools"][0]["name"] == "search"
        assert result["tool_choice"] == "auto"

    def test_default_instructions(self):
        body = {"messages": [{"role": "user", "content": "Hi"}]}
        result = translate_request(body)
        assert result["instructions"] == "You are a helpful assistant."

    def test_strips_anthropic_specific_fields(self):
        body = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "temperature": 0.7,
            "messages": [{"role": "user", "content": "Hi"}],
        }
        result = translate_request(body)
        assert "max_tokens" not in result
        assert "temperature" not in result
        assert "model" in result
        assert result["_original_model"] == "claude-sonnet-4-20250514"


# ── AnthropicSSEBuilder ───────────────────────────────────────────────────


class TestAnthropicSSEBuilder:
    def test_message_start(self):
        builder = AnthropicSSEBuilder(model="test-model")
        out = builder.message_start()
        assert "event: message_start" in out
        data = json.loads(out.split("data: ")[1].split("\n")[0])
        assert data["message"]["role"] == "assistant"
        assert data["message"]["model"] == "test-model"

    def test_text_delta_auto_starts_block(self):
        builder = AnthropicSSEBuilder()
        out = builder.text_delta("hello")
        assert "event: content_block_start" in out
        assert "event: content_block_delta" in out
        data_lines = [l for l in out.split("\n") if l.startswith("data: ")]
        delta_data = json.loads(data_lines[-1].removeprefix("data: "))
        assert delta_data["delta"]["text"] == "hello"

    def test_second_text_delta_no_new_block(self):
        builder = AnthropicSSEBuilder()
        builder.text_delta("first")
        out = builder.text_delta("second")
        assert "content_block_start" not in out
        assert "content_block_delta" in out

    def test_tool_use_stops_text_block(self):
        builder = AnthropicSSEBuilder()
        builder.text_delta("some text")
        out = builder.start_tool_use("call_1", "search")
        assert "content_block_stop" in out
        assert "content_block_start" in out
        data_lines = [l for l in out.split("\n") if l.startswith("data: ")]
        start_data = json.loads(data_lines[-1].removeprefix("data: "))
        assert start_data["content_block"]["type"] == "tool_use"
        assert start_data["content_block"]["name"] == "search"
        assert start_data["content_block"]["id"] == "call_1"

    def test_tool_args_delta(self):
        builder = AnthropicSSEBuilder()
        builder.start_tool_use("call_1", "search")
        out = builder.tool_args_delta('{"q": "test"}')
        assert "input_json_delta" in out
        data = json.loads(out.split("data: ")[1].split("\n")[0])
        assert data["delta"]["partial_json"] == '{"q": "test"}'

    def test_message_end(self):
        builder = AnthropicSSEBuilder()
        builder.text_delta("text")
        out = builder.message_end("end_turn")
        assert "content_block_stop" in out
        assert "event: message_delta" in out
        assert "event: message_stop" in out

    def test_error_event(self):
        builder = AnthropicSSEBuilder()
        out = builder.error_event("something broke")
        data = json.loads(out.split("data: ")[1].split("\n")[0])
        assert data["type"] == "error"
        assert data["error"]["message"] == "something broke"


# ── translate_sse_event ───────────────────────────────────────────────────


class TestTranslateSSEEvent:
    def test_text_delta(self):
        builder = AnthropicSSEBuilder()
        out = translate_sse_event(
            "response.output_text.delta", {"delta": "hi"}, builder
        )
        assert "text_delta" in out
        assert "hi" in out

    def test_text_done_ignored(self):
        builder = AnthropicSSEBuilder()
        out = translate_sse_event("response.output_text.done", {}, builder)
        assert out == ""

    def test_function_call_args_delta(self):
        builder = AnthropicSSEBuilder()
        builder.start_tool_use("call_1", "fn")
        out = translate_sse_event(
            "response.function_call_arguments.delta", {"delta": '{"a":'}, builder
        )
        assert "input_json_delta" in out

    def test_output_item_added_function_call(self):
        builder = AnthropicSSEBuilder()
        out = translate_sse_event(
            "response.output_item.added",
            {"item": {"type": "function_call", "call_id": "c1", "name": "search"}},
            builder,
        )
        assert "tool_use" in out
        assert "search" in out

    def test_output_item_added_non_function(self):
        builder = AnthropicSSEBuilder()
        out = translate_sse_event(
            "response.output_item.added",
            {"item": {"type": "message"}},
            builder,
        )
        assert out == ""

    def test_output_item_done_stops_tool_block(self):
        builder = AnthropicSSEBuilder()
        builder.start_tool_use("c1", "fn")
        assert builder.in_tool_block
        out = translate_sse_event("response.output_item.done", {}, builder)
        assert "content_block_stop" in out
        assert not builder.in_tool_block

    def test_response_completed_end_turn(self):
        builder = AnthropicSSEBuilder()
        builder.text_delta("text")
        out = translate_sse_event(
            "response.completed",
            {"response": {"status": "completed", "output": []}},
            builder,
        )
        assert "message_delta" in out
        assert "message_stop" in out
        assert "end_turn" in out

    def test_response_completed_tool_use(self):
        builder = AnthropicSSEBuilder()
        out = translate_sse_event(
            "response.completed",
            {
                "response": {
                    "status": "completed",
                    "output": [{"type": "function_call"}],
                }
            },
            builder,
        )
        assert "tool_use" in out

    def test_response_failed(self):
        builder = AnthropicSSEBuilder()
        out = translate_sse_event(
            "response.failed",
            {"response": {"error": {"message": "rate limited"}}},
            builder,
        )
        assert "rate limited" in out

    def test_error_event(self):
        builder = AnthropicSSEBuilder()
        out = translate_sse_event("error", {"message": "bad request"}, builder)
        assert "bad request" in out

    def test_unknown_event_ignored(self):
        builder = AnthropicSSEBuilder()
        out = translate_sse_event("response.some_unknown_event", {}, builder)
        assert out == ""

    def test_reasoning_summary_dropped(self):
        builder = AnthropicSSEBuilder()
        out = translate_sse_event(
            "response.reasoning_summary_text.delta", {"delta": "thinking..."}, builder
        )
        assert out == ""
