"""Tests for LLM completion status and finish reason handling.

Verifies:
- finish_reason is captured across non-streaming and streaming provider paths.
- Responses with finish_reason="length" (truncated) reject tool calls and
  produce synthetic error tool results while preserving call/result pairing.
- Disconnected streams with partial tool calls are marked with finish_reason="error"
  and reject tool execution.
- Responses with normal finish_reason="stop" or "tool_calls" execute tools normally.
- Streaming truncation behaves identically to non-streaming truncation.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from decafclaw.agent import run_agent_turn
from decafclaw.config import Config
from decafclaw.config_types import LlmConfig
from decafclaw.llm.providers.openai_compat import OpenAICompatProvider
from decafclaw.llm.types import (
    FINISH_REASON_CANCELLED,
    FINISH_REASON_ERROR,
    FINISH_REASON_LENGTH,
    FINISH_REASON_STOP,
    FINISH_REASON_TOOL_CALLS,
)
from decafclaw.media import ToolResult


class FakeSSEEvent:
    """Simulates an httpx-sse ServerSentEvent."""

    def __init__(self, data: str):
        self.data = data


class FakeResponse:
    """Simulates an httpx Response."""

    def __init__(self, status_code: int = 200, body: bytes = b""):
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self._body = body

    async def aread(self) -> bytes:
        return self._body


class FakeEventSource:
    """Simulates an httpx-sse event source yielding events."""

    def __init__(self, events: list, status_code: int = 200):
        self._events = events
        self.response = FakeResponse(status_code)

    async def aiter_sse(self):
        for event in self._events:
            yield event

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class ErrorEventSource:
    """Event source that raises after yielding initial events."""

    def __init__(self, events_before_error: list, error: Exception):
        self._events = events_before_error
        self._error = error
        self.response = FakeResponse(200)

    async def aiter_sse(self):
        for event in self._events:
            yield event
        raise self._error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _test_config(streaming: bool = False) -> Config:
    """Minimal test config."""
    return Config(llm=LlmConfig(url="http://test/v1/chat/completions", streaming=streaming))


# ---------------------------------------------------------------------------
# Provider unit tests: finish_reason capture
#
# These exercise the real OpenAICompatProvider.complete with httpx/httpx_sse
# mocked, so they opt out of the conftest unstubbed-call guard via live_llm
# (same approach as tests/test_llm_streaming.py). No network calls are made.
# ---------------------------------------------------------------------------


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_provider_non_streaming_captures_finish_reason_stop():
    """Non-streaming completions preserve finish_reason='stop'."""
    provider = OpenAICompatProvider(url="http://test/v1/chat/completions", api_key="dummy")
    resp_data = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    mock_resp = httpx.Response(200, json=resp_data, request=httpx.Request("POST", "http://test"))
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        res = await provider.complete("test-model", [{"role": "user", "content": "hi"}])

    assert res["finish_reason"] == FINISH_REASON_STOP
    assert res["content"] == "Hello!"


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_provider_non_streaming_captures_finish_reason_length():
    """Non-streaming completions preserve finish_reason='length'."""
    provider = OpenAICompatProvider(url="http://test/v1/chat/completions", api_key="dummy")
    resp_data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "test_tool", "arguments": '{"param": 1'},
                        }
                    ],
                },
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 50},
    }

    mock_resp = httpx.Response(200, json=resp_data, request=httpx.Request("POST", "http://test"))
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        res = await provider.complete("test-model", [{"role": "user", "content": "hi"}])

    assert res["finish_reason"] == FINISH_REASON_LENGTH
    assert len(res["tool_calls"]) == 1
    assert res["tool_calls"][0]["id"] == "call_abc"


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_provider_streaming_captures_finish_reason_stop():
    """Streaming completions capture finish_reason='stop' from choices."""
    provider = OpenAICompatProvider(url="http://test/v1/chat/completions", api_key="dummy")
    events = [
        FakeSSEEvent(json.dumps({"choices": [{"delta": {"content": "Hello "}}]})),
        FakeSSEEvent(json.dumps({"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]})),
        FakeSSEEvent("[DONE]"),
    ]

    with patch("httpx_sse.aconnect_sse", return_value=FakeEventSource(events)):
        res = await provider.complete("test-model", [], streaming=True)

    assert res["content"] == "Hello world"
    assert res["finish_reason"] == FINISH_REASON_STOP


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_provider_streaming_captures_finish_reason_length():
    """Streaming completions capture finish_reason='length' when truncated."""
    provider = OpenAICompatProvider(url="http://test/v1/chat/completions", api_key="dummy")
    events = [
        FakeSSEEvent(json.dumps({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_123",
                        "function": {"name": "memory_search", "arguments": '{"query": "test"}'},
                    }]
                }
            }]
        })),
        FakeSSEEvent(json.dumps({
            "choices": [{"delta": {}, "finish_reason": "length"}],
        })),
        FakeSSEEvent("[DONE]"),
    ]

    with patch("httpx_sse.aconnect_sse", return_value=FakeEventSource(events)):
        res = await provider.complete("test-model", [], streaming=True)

    assert res["finish_reason"] == FINISH_REASON_LENGTH
    assert res["tool_calls"] is not None
    assert len(res["tool_calls"]) == 1


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_provider_streaming_disconnected_stream_marks_error():
    """A streaming connection error after receiving partial tool calls sets finish_reason='error'."""
    provider = OpenAICompatProvider(url="http://test/v1/chat/completions", api_key="dummy")
    partial_events = [
        FakeSSEEvent(json.dumps({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_123",
                        "function": {"name": "memory_search", "arguments": '{"query": "test"}'},
                    }]
                }
            }]
        })),
    ]

    with patch("httpx_sse.aconnect_sse", return_value=ErrorEventSource(partial_events, httpx.ReadError("connection drop"))):
        res = await provider.complete("test-model", [], streaming=True)

    assert res["finish_reason"] == FINISH_REASON_ERROR
    assert res["tool_calls"] is not None
    assert len(res["tool_calls"]) == 1


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_provider_streaming_cancelled_sets_finish_reason():
    """Streaming cancelled via cancel_event sets finish_reason='cancelled'."""
    import asyncio
    cancel_event = asyncio.Event()
    cancel_event.set()

    provider = OpenAICompatProvider(url="http://test/v1/chat/completions", api_key="dummy")
    events = [
        FakeSSEEvent(json.dumps({"choices": [{"delta": {"content": "Hello"}}]})),
    ]

    with patch("httpx_sse.aconnect_sse", return_value=FakeEventSource(events)):
        res = await provider.complete("test-model", [], streaming=True, cancel_event=cancel_event)

    assert res["finish_reason"] == FINISH_REASON_CANCELLED


# ---------------------------------------------------------------------------
# Agent turn integration tests: tool dispatch gating on finish_reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_turn_rejects_truncated_tool_call(ctx):
    """When finish_reason='length', tool calls are NOT executed and synthetic error results are produced."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."

    truncated_response = {
        "content": "I will search memories",
        "tool_calls": [{
            "id": "call_trunc_1",
            "type": "function",
            "function": {"name": "memory_search", "arguments": '{"query": "hello"}'},
        }],
        "role": "assistant",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "finish_reason": FINISH_REASON_LENGTH,
    }
    final_response = {
        "content": "Recovered after reissue.",
        "tool_calls": None,
        "role": "assistant",
        "usage": {"prompt_tokens": 120, "completion_tokens": 20},
        "finish_reason": FINISH_REASON_STOP,
    }

    tool_executed = False

    async def fake_execute_tool(call_ctx, fn_name, fn_args):
        nonlocal tool_executed
        tool_executed = True
        return ToolResult(text="search result")

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.tool_execution.execute_tool", side_effect=fake_execute_tool):
        mock_llm.side_effect = [truncated_response, final_response]
        history: list[dict] = []
        result = await run_agent_turn(ctx, "find things", history)

    # Tool implementation was NOT executed
    assert not tool_executed
    assert result.text == "Recovered after reissue."

    # Verify history and message pairing:
    # 0: user message
    # 1: assistant message with tool_calls
    # 2: tool message with matching tool_call_id and error message
    # 3: assistant final message
    assert len(history) == 4
    assert history[0]["role"] == "user"

    assistant_msg = history[1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == "I will search memories"
    assert len(assistant_msg["tool_calls"]) == 1
    assert assistant_msg["tool_calls"][0]["id"] == "call_trunc_1"

    tool_msg = history[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_trunc_1"
    expected_error = (
        "[error: tool call was not executed because the model response was "
        "truncated at the token limit. Please reissue the call.]"
    )
    assert tool_msg["content"] == expected_error

    assert history[3]["role"] == "assistant"
    assert history[3]["content"] == "Recovered after reissue."


@pytest.mark.asyncio
async def test_agent_turn_rejects_multiple_truncated_tool_calls(ctx):
    """Multiple tool calls in a length-truncated response all receive synthetic error results."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."

    truncated_response = {
        "content": None,
        "tool_calls": [
            {
                "id": "tc_1",
                "type": "function",
                "function": {"name": "tool_a", "arguments": "{}"},
            },
            {
                "id": "tc_2",
                "type": "function",
                "function": {"name": "tool_b", "arguments": "{}"},
            },
        ],
        "role": "assistant",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "finish_reason": FINISH_REASON_LENGTH,
    }
    final_response = {
        "content": "All done.",
        "tool_calls": None,
        "role": "assistant",
        "usage": {"prompt_tokens": 120, "completion_tokens": 20},
        "finish_reason": FINISH_REASON_STOP,
    }

    tool_called = False

    async def fake_execute_tool(call_ctx, fn_name, fn_args):
        nonlocal tool_called
        tool_called = True
        return ToolResult(text="ok")

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.tool_execution.execute_tool", side_effect=fake_execute_tool):
        mock_llm.side_effect = [truncated_response, final_response]
        history: list[dict] = []
        result = await run_agent_turn(ctx, "run tools", history)

    assert not tool_called
    assert result.text == "All done."

    # history: user, assistant(tc_1, tc_2), tool(tc_1), tool(tc_2), assistant
    assert len(history) == 5
    assert history[1]["role"] == "assistant"
    assert len(history[1]["tool_calls"]) == 2

    assert history[2]["role"] == "tool"
    assert history[2]["tool_call_id"] == "tc_1"
    assert "truncated at the token limit" in history[2]["content"]

    assert history[3]["role"] == "tool"
    assert history[3]["tool_call_id"] == "tc_2"
    assert "truncated at the token limit" in history[3]["content"]


@pytest.mark.asyncio
async def test_agent_turn_executes_tool_call_on_stop(ctx):
    """When finish_reason='stop', tool calls execute normally."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."

    normal_tool_response = {
        "content": None,
        "tool_calls": [{
            "id": "call_normal_1",
            "type": "function",
            "function": {"name": "memory_recent", "arguments": '{"n": 1}'},
        }],
        "role": "assistant",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "finish_reason": FINISH_REASON_STOP,
    }
    final_response = {
        "content": "Here is the memory.",
        "tool_calls": None,
        "role": "assistant",
        "usage": {"prompt_tokens": 110, "completion_tokens": 30},
        "finish_reason": FINISH_REASON_STOP,
    }

    tool_executed = False

    async def fake_execute_tool(call_ctx, fn_name, fn_args):
        nonlocal tool_executed
        tool_executed = True
        return ToolResult(text="memory result 123")

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.tool_execution.execute_tool", side_effect=fake_execute_tool):
        mock_llm.side_effect = [normal_tool_response, final_response]
        history: list[dict] = []
        result = await run_agent_turn(ctx, "show memory", history)

    assert tool_executed
    assert result.text == "Here is the memory."
    assert len(history) == 4
    assert history[2]["role"] == "tool"
    assert history[2]["tool_call_id"] == "call_normal_1"
    assert "memory result 123" in history[2]["content"]


@pytest.mark.asyncio
async def test_agent_turn_executes_tool_call_on_tool_calls_reason(ctx):
    """When finish_reason='tool_calls', tool calls execute normally."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."

    normal_tool_response = {
        "content": None,
        "tool_calls": [{
            "id": "call_tc_1",
            "type": "function",
            "function": {"name": "memory_recent", "arguments": '{"n": 1}'},
        }],
        "role": "assistant",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "finish_reason": FINISH_REASON_TOOL_CALLS,
    }
    final_response = {
        "content": "Got it.",
        "tool_calls": None,
        "role": "assistant",
        "usage": {"prompt_tokens": 110, "completion_tokens": 30},
        "finish_reason": FINISH_REASON_STOP,
    }

    tool_executed = False

    async def fake_execute_tool(call_ctx, fn_name, fn_args):
        nonlocal tool_executed
        tool_executed = True
        return ToolResult(text="memory result 456")

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.tool_execution.execute_tool", side_effect=fake_execute_tool):
        mock_llm.side_effect = [normal_tool_response, final_response]
        history: list[dict] = []
        result = await run_agent_turn(ctx, "check", history)

    assert tool_executed
    assert result.text == "Got it."
    assert history[2]["role"] == "tool"
    assert history[2]["tool_call_id"] == "call_tc_1"
    assert "memory result 456" in history[2]["content"]


@pytest.mark.asyncio
async def test_agent_turn_rejects_disconnected_stream_tool_calls(ctx):
    """A tool call from a disconnected stream (finish_reason='error') is rejected with an error tool result."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."

    error_response = {
        "content": None,
        "tool_calls": [{
            "id": "call_stream_err",
            "type": "function",
            "function": {"name": "dangerous_action", "arguments": '{"key": "val"}'},
        }],
        "role": "assistant",
        "usage": None,
        "finish_reason": FINISH_REASON_ERROR,
    }
    final_response = {
        "content": "Stream recovered.",
        "tool_calls": None,
        "role": "assistant",
        "usage": None,
        "finish_reason": FINISH_REASON_STOP,
    }

    tool_executed = False

    async def fake_execute_tool(call_ctx, fn_name, fn_args):
        nonlocal tool_executed
        tool_executed = True
        return ToolResult(text="should not execute")

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.tool_execution.execute_tool", side_effect=fake_execute_tool):
        mock_llm.side_effect = [error_response, final_response]
        history: list[dict] = []
        result = await run_agent_turn(ctx, "do action", history)

    assert not tool_executed
    assert result.text == "Stream recovered."
    assert len(history) == 4
    tool_msg = history[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_stream_err"
    assert "interrupted" in tool_msg["content"]


@pytest.mark.asyncio
async def test_streaming_truncation_behaves_identically_to_non_streaming(ctx):
    """Streaming truncation produces the exact same rejection and history pairing."""
    ctx.config.llm.streaming = True
    ctx.config.system_prompt = "You are a test bot."

    streaming_trunc_response = {
        "content": "Analyzing...",
        "tool_calls": [{
            "id": "call_stream_trunc",
            "type": "function",
            "function": {"name": "run_analysis", "arguments": '{"depth": 3}'},
        }],
        "role": "assistant",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "finish_reason": FINISH_REASON_LENGTH,
    }
    streaming_final_response = {
        "content": "Final streaming reply.",
        "tool_calls": None,
        "role": "assistant",
        "usage": {"prompt_tokens": 130, "completion_tokens": 20},
        "finish_reason": FINISH_REASON_STOP,
    }

    tool_executed = False

    async def fake_execute_tool(call_ctx, fn_name, fn_args):
        nonlocal tool_executed
        tool_executed = True
        return ToolResult(text="never run")

    with patch("decafclaw.llm.call_llm_streaming", new_callable=AsyncMock) as mock_streaming, \
         patch("decafclaw.tool_execution.execute_tool", side_effect=fake_execute_tool):
        mock_streaming.side_effect = [streaming_trunc_response, streaming_final_response]
        history: list[dict] = []
        result = await run_agent_turn(ctx, "run analysis", history)

    assert not tool_executed
    assert result.text == "Final streaming reply."
    assert len(history) == 4
    assert history[1]["content"] == "Analyzing..."
    tool_msg = history[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_stream_trunc"
    expected_error = (
        "[error: tool call was not executed because the model response was "
        "truncated at the token limit. Please reissue the call.]"
    )
    assert tool_msg["content"] == expected_error
