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


# -- Concurrent tool execution tests -------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_calls_concurrent(ctx):
    """Multiple tool calls run concurrently — verified via barrier synchronization."""
    barrier_reached = asyncio.Event()
    all_started = []

    async def _concurrent_tool(ctx_arg, name, args):
        all_started.append(ctx_arg.current_tool_call_id)
        if len(all_started) >= 3:
            barrier_reached.set()
        # Wait for all to start — proves they're running concurrently
        await asyncio.wait_for(barrier_reached.wait(), timeout=2.0)
        return ToolResult(text="done")

    tool_calls = [
        {
            "id": f"tc{i}",
            "function": {"name": "slow_tool", "arguments": "{}"},
        }
        for i in range(3)
    ]
    history = []
    messages = []
    pending_media = []

    with patch("decafclaw.agent.execute_tool", side_effect=_concurrent_tool):
        result = await _execute_tool_calls(ctx, tool_calls, history, messages, pending_media)

    assert result is None
    assert len(history) == 3
    # All 3 started before any completed — proves concurrency
    assert len(all_started) == 3


@pytest.mark.asyncio
async def test_execute_tool_calls_semaphore_limits(ctx):
    """With max_concurrent_tools=1, execution is effectively sequential."""
    ctx.config.max_concurrent_tools = 1
    concurrency_high_water = 0
    current_concurrency = 0

    async def _tracked_tool(ctx_arg, name, args):
        nonlocal current_concurrency, concurrency_high_water
        current_concurrency += 1
        concurrency_high_water = max(concurrency_high_water, current_concurrency)
        await asyncio.sleep(0.01)
        current_concurrency -= 1
        return ToolResult(text="done")

    tool_calls = [
        {"id": f"tc{i}", "function": {"name": "slow_tool", "arguments": "{}"}}
        for i in range(3)
    ]
    history = []
    messages = []
    pending_media = []

    with patch("decafclaw.agent.execute_tool", side_effect=_tracked_tool):
        await _execute_tool_calls(ctx, tool_calls, history, messages, pending_media)

    # With semaphore=1, max concurrency should be exactly 1
    assert concurrency_high_water == 1


@pytest.mark.asyncio
async def test_execute_tool_calls_one_fails_others_succeed(ctx):
    """One tool failing doesn't prevent others from completing."""

    async def _maybe_fail(ctx_arg, name, args):
        # Fail based on tool_call_id, not execution order
        if ctx_arg.current_tool_call_id == "tc1":
            raise RuntimeError("boom")
        return ToolResult(text=f"ok-{ctx_arg.current_tool_call_id}")

    tool_calls = [
        {"id": f"tc{i}", "function": {"name": "tool", "arguments": "{}"}}
        for i in range(3)
    ]
    history = []
    messages = []
    pending_media = []

    with patch("decafclaw.agent.execute_tool", side_effect=_maybe_fail):
        result = await _execute_tool_calls(ctx, tool_calls, history, messages, pending_media)

    assert result is None
    assert len(history) == 3
    # Results in call order: tc0 succeeded, tc1 failed, tc2 succeeded
    assert "ok-tc0" in history[0]["content"]
    assert "error" in history[1]["content"]
    assert "ok-tc2" in history[2]["content"]


@pytest.mark.asyncio
async def test_execute_tool_calls_preserves_order(ctx):
    """Results are returned in call order, not completion order."""
    async def _variable_speed(ctx_arg, name, args):
        return ToolResult(text=f"result-{ctx_arg.current_tool_call_id}")

    tool_calls = [
        {"id": f"tc{i}", "function": {"name": "tool", "arguments": "{}"}}
        for i in range(3)
    ]
    history = []
    messages = []
    pending_media = []

    with patch("decafclaw.agent.execute_tool", side_effect=_variable_speed):
        await _execute_tool_calls(ctx, tool_calls, history, messages, pending_media)

    assert history[0]["tool_call_id"] == "tc0"
    assert history[1]["tool_call_id"] == "tc1"
    assert history[2]["tool_call_id"] == "tc2"


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
async def test_run_agent_turn_max_iterations_preserves_text(ctx):
    """Max iterations preserves text from tool-call iterations."""
    ctx.config.llm_streaming = False
    ctx.config.system_prompt = "You are a test bot."
    ctx.config.max_tool_iterations = 2

    tool_call_response = _mock_llm_response(
        content="Let me check that for you.",
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

    assert "Let me check that for you." in result.text
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
