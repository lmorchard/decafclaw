"""Tests for agent turn helpers and the core run_agent_turn loop."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.agent import (
    _build_tool_list,
    _check_cancelled,
    _conv_id,
    _execute_tool_calls,
    run_agent_turn,
)
from decafclaw.media import ToolResult

# -- Helper tests --------------------------------------------------------------


def test_conv_id_from_conv_id(ctx):
    ctx.conv_id = "my-conv"
    assert _conv_id(ctx) == "my-conv"


def test_conv_id_falls_back_to_channel_id(ctx):
    ctx.conv_id = None
    ctx.channel_id = "my-channel"
    assert _conv_id(ctx) == "my-channel"


def test_conv_id_falls_back_to_unknown(config):
    """Context with neither conv_id nor channel_id returns 'unknown'."""
    from decafclaw.context import Context
    from decafclaw.events import EventBus
    bare_ctx = Context(config=config, event_bus=EventBus())
    assert _conv_id(bare_ctx) == "unknown"


def test_check_cancelled_returns_none_when_not_cancelled(ctx):
    history = []
    assert _check_cancelled(ctx, history) is None
    assert len(history) == 0


def test_check_cancelled_returns_none_when_no_event(ctx):
    # No cancelled attribute at all
    history = []
    assert _check_cancelled(ctx, history) is None


@pytest.mark.asyncio
async def test_check_cancelled_returns_result_when_cancelled(ctx):
    ctx.cancelled = asyncio.Event()
    ctx.cancelled.set()
    history = []
    result = _check_cancelled(ctx, history)
    assert result is not None
    assert "cancelled" in result.text
    assert len(history) == 1
    assert history[0]["role"] == "assistant"


def test_check_cancelled_not_set(ctx):
    ctx.cancelled = asyncio.Event()
    # Not set
    history = []
    assert _check_cancelled(ctx, history) is None


def test_build_tool_list_base_tools(ctx):
    tools = _build_tool_list(ctx)
    # Should have at least the base tools
    assert len(tools) > 0
    names = [t["function"]["name"] for t in tools]
    assert "memory_save" in names


def test_build_tool_list_with_extra_tools(ctx):
    extra_def = {
        "type": "function",
        "function": {"name": "custom_tool", "parameters": {}},
    }
    ctx.extra_tool_definitions = [extra_def]
    tools = _build_tool_list(ctx)
    names = [t["function"]["name"] for t in tools]
    assert "custom_tool" in names


# -- _execute_tool_calls tests -------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_calls_runs_tools(ctx):
    tool_calls = [
        {
            "id": "tc1",
            "function": {
                "name": "memory_recent",
                "arguments": json.dumps({"n": 3}),
            },
        }
    ]
    history = []
    messages = []
    pending_media = []

    result = await _execute_tool_calls(ctx, tool_calls, history, messages, pending_media)
    assert result is None  # not cancelled
    assert len(history) == 1
    assert history[0]["role"] == "tool"
    assert history[0]["tool_call_id"] == "tc1"


@pytest.mark.asyncio
async def test_execute_tool_calls_handles_malformed_json(ctx):
    tool_calls = [
        {
            "id": "tc1",
            "function": {
                "name": "memory_recent",
                "arguments": "not valid json{{{",
            },
        }
    ]
    history = []
    messages = []
    pending_media = []

    # Should not raise — handles JSONDecodeError gracefully
    result = await _execute_tool_calls(ctx, tool_calls, history, messages, pending_media)
    assert result is None
    assert len(history) == 1


@pytest.mark.asyncio
async def test_execute_tool_calls_cancellation(ctx):
    ctx.cancelled = asyncio.Event()
    ctx.cancelled.set()  # Already cancelled

    tool_calls = [
        {
            "id": "tc1",
            "function": {"name": "memory_recent", "arguments": "{}"},
        }
    ]
    history = []
    messages = []
    pending_media = []

    result = await _execute_tool_calls(ctx, tool_calls, history, messages, pending_media)
    assert result is not None
    assert "cancelled" in result.text


# -- run_agent_turn integration tests ------------------------------------------


def _mock_llm_response(content="Hello!", tool_calls=None, usage=None):
    """Build a mock LLM response dict."""
    return {
        "content": content,
        "tool_calls": tool_calls,
        "role": "assistant",
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50},
    }


@pytest.mark.asyncio
async def test_run_agent_turn_simple_response(ctx):
    """LLM returns a simple text response with no tool calls."""
    ctx.config.llm_streaming = False
    ctx.config.system_prompt = "You are a test bot."

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_llm_response("Hello world!")
        history = []
        result = await run_agent_turn(ctx, "hi", history)

    assert result.text == "Hello world!"
    assert len(history) == 2  # user msg + assistant msg
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hi"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "Hello world!"


@pytest.mark.asyncio
async def test_run_agent_turn_with_tool_call(ctx):
    """LLM calls a tool, gets result, then responds."""
    ctx.config.llm_streaming = False
    ctx.config.system_prompt = "You are a test bot."

    tool_call_response = _mock_llm_response(
        content=None,
        tool_calls=[{
            "id": "tc1",
            "function": {
                "name": "memory_recent",
                "arguments": json.dumps({"n": 1}),
            },
        }],
    )
    final_response = _mock_llm_response("Here are your memories.")

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [tool_call_response, final_response]
        history = []
        result = await run_agent_turn(ctx, "show memories", history)

    assert result.text == "Here are your memories."
    # history: user, assistant (tool call), tool result, assistant (final)
    assert len(history) == 4
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    assert "tool_calls" in history[1]
    assert history[2]["role"] == "tool"
    assert history[3]["role"] == "assistant"


@pytest.mark.asyncio
async def test_run_agent_turn_cancellation(ctx):
    """Turn is cancelled before LLM is called."""
    ctx.config.llm_streaming = False
    ctx.config.system_prompt = "You are a test bot."
    ctx.cancelled = asyncio.Event()
    ctx.cancelled.set()

    history = []
    result = await run_agent_turn(ctx, "hi", history)
    assert "cancelled" in result.text


@pytest.mark.asyncio
async def test_run_agent_turn_max_iterations(ctx):
    """LLM keeps calling tools until max iterations is reached."""
    ctx.config.llm_streaming = False
    ctx.config.system_prompt = "You are a test bot."
    ctx.config.max_tool_iterations = 2

    tool_call_response = _mock_llm_response(
        content=None,
        tool_calls=[{
            "id": "tc1",
            "function": {
                "name": "memory_recent",
                "arguments": "{}",
            },
        }],
    )

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = tool_call_response
        history = []
        result = await run_agent_turn(ctx, "loop forever", history)

    assert "max tool iterations" in result.text


@pytest.mark.asyncio
async def test_run_agent_turn_empty_response(ctx):
    """LLM returns empty content with no tool calls."""
    ctx.config.llm_streaming = False
    ctx.config.system_prompt = "You are a test bot."

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_llm_response(content="")
        history = []
        result = await run_agent_turn(ctx, "hi", history)

    assert result.text == ""


@pytest.mark.asyncio
async def test_run_agent_turn_tracks_token_usage(ctx):
    """Token usage from LLM response is accumulated on context."""
    ctx.config.llm_streaming = False
    ctx.config.system_prompt = "You are a test bot."

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_llm_response(
            "hi", usage={"prompt_tokens": 200, "completion_tokens": 50}
        )
        history = []
        await run_agent_turn(ctx, "hi", history)

    assert ctx.total_prompt_tokens == 200
    assert ctx.total_completion_tokens == 50


@pytest.mark.asyncio
async def test_run_agent_turn_archives_messages(ctx):
    """Messages are archived during the turn."""
    ctx.config.llm_streaming = False
    ctx.config.system_prompt = "You are a test bot."

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_llm_response("response")
        history = []
        await run_agent_turn(ctx, "hi", history)

    # Check archive file was written
    from decafclaw.archive import read_archive
    archived = read_archive(ctx.config, "test-conv")
    assert len(archived) == 2
    assert archived[0]["role"] == "user"
    assert archived[1]["role"] == "assistant"
