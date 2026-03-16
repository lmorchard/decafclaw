"""Tests for LLM streaming — SSE parsing, tool call assembly, callbacks."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from decafclaw.llm import _sanitize_tool_call_id, call_llm_streaming


class FakeSSEEvent:
    """Simulates an httpx-sse ServerSentEvent."""
    def __init__(self, data):
        self.data = data


class FakeEventSource:
    """Simulates an httpx-sse event source that yields events."""
    def __init__(self, events):
        self._events = events

    async def aiter_sse(self):
        for event in self._events:
            yield event

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_text_events(tokens, usage=None):
    """Create SSE events for a simple text response."""
    events = []
    for token in tokens:
        events.append(FakeSSEEvent(json.dumps({
            "choices": [{"delta": {"content": token}}],
        })))
    if usage:
        events.append(FakeSSEEvent(json.dumps({
            "choices": [{"delta": {}}],
            "usage": usage,
        })))
    events.append(FakeSSEEvent("[DONE]"))
    return events


def _make_tool_call_events(name, arguments_chunks, tool_id="call_0", index=0):
    """Create SSE events for a tool call."""
    events = []
    # First chunk: name + start of arguments
    events.append(FakeSSEEvent(json.dumps({
        "choices": [{"delta": {"tool_calls": [{
            "index": index,
            "id": tool_id,
            "function": {"name": name, "arguments": arguments_chunks[0]},
        }]}}],
    })))
    # Subsequent chunks: argument deltas
    for chunk in arguments_chunks[1:]:
        events.append(FakeSSEEvent(json.dumps({
            "choices": [{"delta": {"tool_calls": [{
                "index": index,
                "function": {"arguments": chunk},
            }]}}],
        })))
    events.append(FakeSSEEvent("[DONE]"))
    return events


def _config():
    """Minimal config for testing."""
    from decafclaw.config import Config
    return Config(llm_url="http://test/v1/chat/completions")


@pytest.mark.asyncio
async def test_streaming_text_only():
    """Text-only streaming accumulates content and calls on_chunk."""
    events = _make_text_events(["Hello", " ", "world"])
    chunks_received = []

    async def on_chunk(chunk_type, data):
        chunks_received.append((chunk_type, data))

    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = FakeEventSource(events)
        result = await call_llm_streaming(_config(), [], on_chunk=on_chunk)

    assert result["content"] == "Hello world"
    assert result["tool_calls"] is None
    text_chunks = [(t, d) for t, d in chunks_received if t == "text"]
    assert len(text_chunks) == 3
    assert text_chunks[0] == ("text", "Hello")
    assert ("done", {"usage": None}) in chunks_received


@pytest.mark.asyncio
async def test_streaming_with_usage():
    """Usage from final chunk is captured."""
    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    events = _make_text_events(["Hi"], usage=usage)

    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = FakeEventSource(events)
        result = await call_llm_streaming(_config(), [])

    assert result["usage"] == usage


@pytest.mark.asyncio
async def test_streaming_no_usage():
    """Missing usage returns None."""
    events = _make_text_events(["Hi"])

    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = FakeEventSource(events)
        result = await call_llm_streaming(_config(), [])

    assert result["usage"] is None


@pytest.mark.asyncio
async def test_streaming_tool_calls():
    """Tool calls are assembled from deltas."""
    events = _make_tool_call_events("memory_search", ['{"query":', '"cats"}'])
    chunks_received = []

    async def on_chunk(chunk_type, data):
        chunks_received.append((chunk_type, data))

    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = FakeEventSource(events)
        result = await call_llm_streaming(_config(), [], on_chunk=on_chunk)

    assert result["tool_calls"] is not None
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["function"]["name"] == "memory_search"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"query":"cats"}'

    # Verify callback sequence
    types = [t for t, d in chunks_received]
    assert "tool_call_start" in types
    assert "tool_call_end" in types
    assert "done" in types


@pytest.mark.asyncio
async def test_streaming_mixed_text_and_tools():
    """Mixed text + tool calls both work."""
    events = [
        FakeSSEEvent(json.dumps({"choices": [{"delta": {"content": "Let me search"}}]})),
        FakeSSEEvent(json.dumps({"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1",
            "function": {"name": "search", "arguments": "{}"},
        }]}}]})),
        FakeSSEEvent("[DONE]"),
    ]
    chunks_received = []

    async def on_chunk(chunk_type, data):
        chunks_received.append((chunk_type, data))

    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = FakeEventSource(events)
        result = await call_llm_streaming(_config(), [], on_chunk=on_chunk)

    assert result["content"] == "Let me search"
    assert result["tool_calls"] is not None
    assert result["tool_calls"][0]["function"]["name"] == "search"

    types = [t for t, d in chunks_received]
    assert types[0] == "text"
    assert "tool_call_start" in types


@pytest.mark.asyncio
async def test_streaming_no_callback():
    """on_chunk=None doesn't error."""
    events = _make_text_events(["Hello"])

    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = FakeEventSource(events)
        result = await call_llm_streaming(_config(), [], on_chunk=None)

    assert result["content"] == "Hello"


@pytest.mark.asyncio
async def test_streaming_callback_error_doesnt_kill_stream():
    """Errors in on_chunk don't stop the stream."""
    events = _make_text_events(["Hello", " world"])

    async def bad_callback(chunk_type, data):
        if chunk_type == "text" and data == "Hello":
            raise RuntimeError("callback broke")

    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = FakeEventSource(events)
        result = await call_llm_streaming(_config(), [], on_chunk=bad_callback)

    # Stream should complete despite callback error
    assert result["content"] == "Hello world"


# -- Tool call ID sanitization ------------------------------------------------


def test_sanitize_tool_call_id_strips_thought():
    """Strips __thought__ suffix and embedded thinking data."""
    bloated = "call_abc123__thought__CiUBjz1rX08dN2FAqXPJEb5RHCverhk3Y0b6"
    assert _sanitize_tool_call_id(bloated) == "call_abc123"


def test_sanitize_tool_call_id_preserves_normal():
    """Normal IDs pass through unchanged."""
    assert _sanitize_tool_call_id("call_abc123") == "call_abc123"


def test_sanitize_tool_call_id_empty():
    """Empty string passes through."""
    assert _sanitize_tool_call_id("") == ""


@pytest.mark.asyncio
async def test_streaming_tool_call_id_sanitized():
    """Streaming tool calls with __thought__ IDs get sanitized."""
    bloated_id = "call_xyz__thought__CiUBjz1rXlongbase64data"
    events = _make_tool_call_events("memory_search", ['{}'], tool_id=bloated_id)

    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = FakeEventSource(events)
        result = await call_llm_streaming(_config(), [])

    assert result["tool_calls"] is not None
    assert result["tool_calls"][0]["id"] == "call_xyz"


# -- Streaming error handling --------------------------------------------------


class ErrorEventSource:
    """Event source that raises after yielding some events."""
    def __init__(self, events_before_error, error):
        self._events = events_before_error
        self._error = error

    async def aiter_sse(self):
        for event in self._events:
            yield event
        raise self._error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_streaming_error_raises_when_nothing_accumulated():
    """Connection error with no accumulated content re-raises."""
    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = ErrorEventSource([], httpx.ConnectError("connection refused"))
        with pytest.raises(httpx.ConnectError, match="connection refused"):
            await call_llm_streaming(_config(), [])


@pytest.mark.asyncio
async def test_streaming_error_returns_partial_on_partial_content():
    """Mid-stream error returns partial content instead of raising."""
    partial_events = [
        FakeSSEEvent(json.dumps({"choices": [{"delta": {"content": "Hello"}}]})),
    ]
    with patch("httpx_sse.aconnect_sse") as mock_sse:
        mock_sse.return_value = ErrorEventSource(
            partial_events, httpx.ReadError("stream interrupted")
        )
        result = await call_llm_streaming(_config(), [])

    # Should return the partial content, not raise
    assert result["content"] == "Hello"
