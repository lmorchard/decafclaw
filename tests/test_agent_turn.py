"""Tests for agent turn helpers and the core run_agent_turn loop."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.agent import (
    _archive,
    _build_tool_list,
    _check_cancelled,
    _conv_id,
    _execute_tool_calls,
    _refresh_dynamic_tools,
    run_agent_turn,
)
from decafclaw.config_types import ReflectionConfig
from decafclaw.media import EndTurnConfirm, ToolResult
from decafclaw.reflection import ReflectionResult

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


def test_archive_skipped_when_flag_set(ctx, config):
    """_archive should not write when ctx.skip_archive is True."""
    from decafclaw.archive import read_archive
    ctx.conv_id = "test-skip-archive"
    msg = {"role": "user", "content": "should not persist"}

    # Normal archive writes
    _archive(ctx, msg)
    assert len(read_archive(config, "test-skip-archive")) == 1

    # With skip_archive, nothing new is written
    ctx.skip_archive = True
    _archive(ctx, {"role": "assistant", "content": "also skipped"})
    assert len(read_archive(config, "test-skip-archive")) == 1


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
    tools, deferred_text = _build_tool_list(ctx)
    # Should have some active tools and may defer some with max_active_tools
    assert len(tools) > 0
    names = [t["function"]["name"] for t in tools]
    # Core tools that are always-loaded should be present
    assert "current_time" in names


def test_build_tool_list_with_extra_tools(ctx):
    extra_def = {
        "type": "function",
        "function": {"name": "custom_tool", "parameters": {}},
    }
    ctx.tools.extra_definitions = [extra_def]
    tools, _ = _build_tool_list(ctx)
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
    cancelled, end_turn = await _execute_tool_calls(ctx, tool_calls, history, messages)
    assert cancelled is None  # not cancelled
    assert end_turn is False
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

    # Should not raise — handles JSONDecodeError gracefully
    cancelled, _ = await _execute_tool_calls(ctx, tool_calls, history, messages)
    assert cancelled is None
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
    cancelled, _ = await _execute_tool_calls(ctx, tool_calls, history, messages)
    assert cancelled is not None
    assert "cancelled" in cancelled.text


# -- Concurrent tool execution tests -------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_calls_concurrent(ctx):
    """Multiple tool calls run concurrently — verified via barrier synchronization."""
    barrier_reached = asyncio.Event()
    all_started = []

    async def _concurrent_tool(ctx_arg, name, args):
        all_started.append(ctx_arg.tools.current_call_id)
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


    with patch("decafclaw.agent.execute_tool", side_effect=_concurrent_tool):
        cancelled, _ = await _execute_tool_calls(ctx, tool_calls, history, messages)

    assert cancelled is None
    assert len(history) == 3
    # All 3 started before any completed — proves concurrency
    assert len(all_started) == 3


@pytest.mark.asyncio
async def test_execute_tool_calls_semaphore_limits(ctx):
    """With max_concurrent_tools=1, execution is effectively sequential."""
    ctx.config.agent.max_concurrent_tools = 1
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


    with patch("decafclaw.agent.execute_tool", side_effect=_tracked_tool):
        await _execute_tool_calls(ctx, tool_calls, history, messages)

    # With semaphore=1, max concurrency should be exactly 1
    assert concurrency_high_water == 1


@pytest.mark.asyncio
async def test_execute_tool_calls_one_fails_others_succeed(ctx):
    """One tool failing doesn't prevent others from completing."""

    async def _maybe_fail(ctx_arg, name, args):
        # Fail based on tool_call_id, not execution order
        if ctx_arg.tools.current_call_id == "tc1":
            raise RuntimeError("boom")
        return ToolResult(text=f"ok-{ctx_arg.tools.current_call_id}")

    tool_calls = [
        {"id": f"tc{i}", "function": {"name": "tool", "arguments": "{}"}}
        for i in range(3)
    ]
    history = []
    messages = []


    with patch("decafclaw.agent.execute_tool", side_effect=_maybe_fail):
        cancelled, _ = await _execute_tool_calls(ctx, tool_calls, history, messages)

    assert cancelled is None
    assert len(history) == 3
    # Results in call order: tc0 succeeded, tc1 failed, tc2 succeeded
    assert "ok-tc0" in history[0]["content"]
    assert "error" in history[1]["content"]
    assert "ok-tc2" in history[2]["content"]


@pytest.mark.asyncio
async def test_execute_tool_calls_preserves_order(ctx):
    """Results are returned in call order, not completion order."""
    async def _variable_speed(ctx_arg, name, args):
        return ToolResult(text=f"result-{ctx_arg.tools.current_call_id}")

    tool_calls = [
        {"id": f"tc{i}", "function": {"name": "tool", "arguments": "{}"}}
        for i in range(3)
    ]
    history = []
    messages = []


    with patch("decafclaw.agent.execute_tool", side_effect=_variable_speed):
        await _execute_tool_calls(ctx, tool_calls, history, messages)

    assert history[0]["tool_call_id"] == "tc0"
    assert history[1]["tool_call_id"] == "tc1"
    assert history[2]["tool_call_id"] == "tc2"


# -- end_turn tests ------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_calls_end_turn_signal(ctx):
    """A tool returning end_turn=True propagates through the batch."""

    async def _end_turn_tool(ctx_arg, name, args):
        return ToolResult(text="spec written", end_turn=True)

    tool_calls = [
        {"id": "tc0", "function": {"name": "tool", "arguments": "{}"}}
    ]
    history = []
    messages = []

    with patch("decafclaw.agent.execute_tool", side_effect=_end_turn_tool):
        cancelled, end_turn = await _execute_tool_calls(ctx, tool_calls, history, messages)

    assert cancelled is None
    assert end_turn is True
    assert len(history) == 1


@pytest.mark.asyncio
async def test_execute_tool_calls_end_turn_in_parallel_batch(ctx):
    """If one tool in a parallel batch signals end_turn, all still execute."""

    async def _mixed_tools(ctx_arg, name, args):
        call_id = ctx_arg.tools.current_call_id
        if call_id == "tc1":
            return ToolResult(text="end here", end_turn=True)
        return ToolResult(text=f"normal-{call_id}")

    tool_calls = [
        {"id": f"tc{i}", "function": {"name": "tool", "arguments": "{}"}}
        for i in range(3)
    ]
    history = []
    messages = []

    with patch("decafclaw.agent.execute_tool", side_effect=_mixed_tools):
        cancelled, end_turn = await _execute_tool_calls(ctx, tool_calls, history, messages)

    assert cancelled is None
    assert end_turn is True
    # All 3 tools executed
    assert len(history) == 3
    assert "normal-tc0" in history[0]["content"]
    assert "end here" in history[1]["content"]
    assert "normal-tc2" in history[2]["content"]


@pytest.mark.asyncio
async def test_execute_tool_calls_no_end_turn_for_bare_strings(ctx):
    """Tools returning bare strings don't trigger end_turn."""

    async def _string_tool(ctx_arg, name, args):
        return "just a string"

    tool_calls = [
        {"id": "tc0", "function": {"name": "tool", "arguments": "{}"}}
    ]
    history = []
    messages = []

    with patch("decafclaw.agent.execute_tool", side_effect=_string_tool):
        cancelled, end_turn = await _execute_tool_calls(ctx, tool_calls, history, messages)

    assert cancelled is None
    assert end_turn is False


@pytest.mark.asyncio
async def test_execute_tool_calls_end_turn_confirm(ctx):
    """EndTurnConfirm propagates through the batch."""
    action = EndTurnConfirm(message="Review spec", approve_label="OK", deny_label="No")

    async def _confirm_tool(ctx_arg, name, args):
        return ToolResult(text="spec ready", end_turn=action)

    tool_calls = [
        {"id": "tc0", "function": {"name": "tool", "arguments": "{}"}}
    ]
    history = []
    messages = []

    with patch("decafclaw.agent.execute_tool", side_effect=_confirm_tool):
        cancelled, end_turn_signal = await _execute_tool_calls(ctx, tool_calls, history, messages)

    assert cancelled is None
    assert isinstance(end_turn_signal, EndTurnConfirm)
    assert end_turn_signal.message == "Review spec"


@pytest.mark.asyncio
async def test_execute_tool_calls_end_turn_confirm_takes_priority(ctx):
    """EndTurnConfirm takes priority over end_turn=True in a batch."""

    async def _mixed_tools(ctx_arg, name, args):
        call_id = ctx_arg.tools.current_call_id
        if call_id == "tc0":
            return ToolResult(text="simple end", end_turn=True)
        return ToolResult(text="confirm", end_turn=EndTurnConfirm(message="review"))

    tool_calls = [
        {"id": "tc0", "function": {"name": "tool", "arguments": "{}"}},
        {"id": "tc1", "function": {"name": "tool", "arguments": "{}"}},
    ]
    history = []
    messages = []

    with patch("decafclaw.agent.execute_tool", side_effect=_mixed_tools):
        cancelled, end_turn_signal = await _execute_tool_calls(ctx, tool_calls, history, messages)

    assert cancelled is None
    assert isinstance(end_turn_signal, EndTurnConfirm)


# -- _refresh_dynamic_tools tests ----------------------------------------------


def test_refresh_dynamic_tools_updates_extra(ctx):
    """Dynamic provider replaces skill tools each call."""
    # Seed initial tools
    ctx.tools.extra = {"tool_a": lambda: None, "other_tool": lambda: None}
    ctx.tools.extra_definitions = [
        {"function": {"name": "tool_a"}},
        {"function": {"name": "other_tool"}},
    ]

    call_count = 0

    def get_tools(c):
        nonlocal call_count
        call_count += 1
        # Return only tool_b this turn (tool_a removed)
        return (
            {"tool_b": lambda: None},
            [{"function": {"name": "tool_b"}}],
        )

    ctx.tools.dynamic_providers = {"my_skill": get_tools}
    # Simulate previous turn having tool_a
    ctx.tools.dynamic_provider_names = {"my_skill": {"tool_a"}}

    _refresh_dynamic_tools(ctx)

    assert call_count == 1
    # tool_a removed, tool_b added, other_tool unchanged
    assert "tool_b" in ctx.tools.extra
    assert "tool_a" not in ctx.tools.extra
    assert "other_tool" in ctx.tools.extra
    # Definitions updated
    def_names = {td["function"]["name"] for td in ctx.tools.extra_definitions}
    assert "tool_b" in def_names
    assert "tool_a" not in def_names
    assert "other_tool" in def_names


def test_refresh_dynamic_tools_noop_without_providers(ctx):
    """No providers means no changes."""
    ctx.tools.extra = {"existing": lambda: None}
    _refresh_dynamic_tools(ctx)
    assert "existing" in ctx.tools.extra


def test_refresh_dynamic_tools_tracks_provider_names(ctx):
    """Provider names are tracked for stale removal on next call."""

    def get_tools(c):
        return (
            {"t1": lambda: None, "t2": lambda: None},
            [{"function": {"name": "t1"}}, {"function": {"name": "t2"}}],
        )

    ctx.tools.dynamic_providers = {"skill": get_tools}
    _refresh_dynamic_tools(ctx)
    assert ctx.tools.dynamic_provider_names["skill"] == {"t1", "t2"}


def test_refresh_dynamic_tools_handles_provider_error(ctx):
    """A failing provider doesn't crash and clears its tracked names."""

    def bad_provider(c):
        raise RuntimeError("boom")

    ctx.tools.dynamic_providers = {"bad": bad_provider}
    ctx.tools.dynamic_provider_names = {"bad": {"old_tool"}}
    ctx.tools.extra = {"old_tool": lambda: None}
    ctx.tools.extra_definitions = [{"function": {"name": "old_tool"}}]

    _refresh_dynamic_tools(ctx)

    # Old tool removed, provider names cleared
    assert "old_tool" not in ctx.tools.extra
    assert ctx.tools.dynamic_provider_names["bad"] == set()


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
    ctx.config.llm.streaming = False
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
    ctx.config.llm.streaming = False
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
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."
    ctx.cancelled = asyncio.Event()
    ctx.cancelled.set()

    history = []
    result = await run_agent_turn(ctx, "hi", history)
    assert "cancelled" in result.text


@pytest.mark.asyncio
async def test_run_agent_turn_max_iterations(ctx):
    """LLM keeps calling tools until max iterations is reached."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."
    ctx.config.agent.max_tool_iterations = 2

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
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."
    ctx.config.agent.max_tool_iterations = 2

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
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_llm_response(content="")
        history = []
        result = await run_agent_turn(ctx, "hi", history)

    assert result.text == ""


@pytest.mark.asyncio
async def test_run_agent_turn_tracks_token_usage(ctx):
    """Token usage from LLM response is accumulated on context."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_llm_response(
            "hi", usage={"prompt_tokens": 200, "completion_tokens": 50}
        )
        history = []
        await run_agent_turn(ctx, "hi", history)

    assert ctx.tokens.total_prompt == 200
    assert ctx.tokens.total_completion == 50


@pytest.mark.asyncio
async def test_run_agent_turn_archives_messages(ctx):
    """Messages are archived during the turn."""
    ctx.config.llm.streaming = False
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


# -- Reflection integration tests --------------------------------------------


@pytest.mark.asyncio
async def test_reflection_pass_delivers_normally(ctx):
    """When the judge passes, the response is delivered as-is."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "test"
    ctx.config.reflection = ReflectionConfig(enabled=True)

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.reflection.evaluate_response", new_callable=AsyncMock) as mock_eval:
        mock_llm.return_value = _mock_llm_response("Good answer")
        mock_eval.return_value = ReflectionResult(passed=True)

        history = []
        result = await run_agent_turn(ctx, "question", history)

    assert result.text == "Good answer"
    assert len(history) == 2  # user + assistant
    mock_eval.assert_called_once()


@pytest.mark.asyncio
async def test_reflection_fail_retries(ctx):
    """When the judge fails, critique is injected and agent retries."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "test"
    ctx.config.reflection = ReflectionConfig(enabled=True, max_retries=2)

    call_count = 0

    async def mock_llm_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_llm_response("Bad answer")
        return _mock_llm_response("Better answer")

    eval_count = 0

    async def mock_eval_side_effect(*args, **kwargs):
        nonlocal eval_count
        eval_count += 1
        if eval_count == 1:
            return ReflectionResult(passed=False, critique="You missed the point")
        return ReflectionResult(passed=True)

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock,
               side_effect=mock_llm_side_effect), \
         patch("decafclaw.reflection.evaluate_response", new_callable=AsyncMock,
               side_effect=mock_eval_side_effect):
        history = []
        result = await run_agent_turn(ctx, "question", history)

    assert result.text == "Better answer"
    # History: user, failed assistant, critique (user), final assistant
    assert len(history) == 4
    assert history[1]["content"] == "Bad answer"
    assert "[reflection]" in history[2]["content"]
    assert history[3]["content"] == "Better answer"


@pytest.mark.asyncio
async def test_reflection_max_retries_delivers_last(ctx):
    """After max retries, deliver the last response regardless."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "test"
    ctx.config.reflection = ReflectionConfig(enabled=True, max_retries=1)

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.reflection.evaluate_response", new_callable=AsyncMock) as mock_eval:
        mock_llm.return_value = _mock_llm_response("Mediocre answer")
        # Always fails
        mock_eval.return_value = ReflectionResult(
            passed=False, critique="Still not great")

        history = []
        result = await run_agent_turn(ctx, "question", history)

    # After 1 retry (max_retries=1), delivers whatever it has (plus escalation nudge)
    assert "Mediocre answer" in result.text
    assert "model picker" in result.text  # escalation nudge appended
    # Judge called once (first attempt), then retry delivers without reflection
    assert mock_eval.call_count == 1


@pytest.mark.asyncio
async def test_reflection_disabled_skips(ctx):
    """Reflection disabled means no judge call."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "test"
    ctx.config.reflection = ReflectionConfig(enabled=False)

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.reflection.evaluate_response", new_callable=AsyncMock) as mock_eval:
        mock_llm.return_value = _mock_llm_response("response")
        history = []
        await run_agent_turn(ctx, "hi", history)

    mock_eval.assert_not_called()


@pytest.mark.asyncio
async def test_reflection_child_skips(ctx):
    """Child agents skip reflection (via skip_reflection flag)."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "test"
    ctx.config.reflection = ReflectionConfig(enabled=True)
    ctx.skip_reflection = True

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.reflection.evaluate_response", new_callable=AsyncMock) as mock_eval:
        mock_llm.return_value = _mock_llm_response("child response")
        history = []
        await run_agent_turn(ctx, "task", history)

    mock_eval.assert_not_called()


@pytest.mark.asyncio
async def test_reflection_error_delivers_response(ctx):
    """If the judge errors, deliver the response as-is."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "test"
    ctx.config.reflection = ReflectionConfig(enabled=True)

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.reflection.evaluate_response", new_callable=AsyncMock) as mock_eval:
        mock_llm.return_value = _mock_llm_response("response")
        mock_eval.return_value = ReflectionResult(
            passed=True, error="connection timeout")

        history = []
        result = await run_agent_turn(ctx, "hi", history)

    assert result.text == "response"
    assert len(history) == 2  # no retry


@pytest.mark.asyncio
async def test_reflection_sees_text_emitted_alongside_tool_calls(ctx):
    """Regression: when the model emits user-visible text alongside tool calls
    (e.g. the postmortem skill's report + vault_write), reflection should
    evaluate the full visible response, not just the trailing no-tools message.

    Before the fix, `evaluate_response` saw only the final-iteration trailer
    ("I have delivered the report"), so the judge failed — it couldn't see the
    report itself — and burned through reflection retries in a loop.
    """
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "test"
    ctx.config.reflection = ReflectionConfig(enabled=True)

    report_text = "Here is the full postmortem report. Anomaly: ..."
    trailer_text = "I have delivered the report and saved it to the vault."

    report_plus_tool = _mock_llm_response(
        content=report_text,
        tool_calls=[{
            "id": "tc1",
            "function": {
                "name": "memory_recent",
                "arguments": json.dumps({"n": 1}),
            },
        }],
    )
    trailer_only = _mock_llm_response(trailer_text)

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.reflection.evaluate_response", new_callable=AsyncMock) as mock_eval:
        mock_llm.side_effect = [report_plus_tool, trailer_only]
        mock_eval.return_value = ReflectionResult(passed=True)

        history = []
        await run_agent_turn(ctx, "/postmortem", history)

    mock_eval.assert_called_once()
    # agent_response is the 3rd positional arg to evaluate_response
    # (see agent.py _handle_reflection: evaluate_response(config, judge_user_message, final_text, ...))
    agent_response_arg = mock_eval.call_args.args[2]
    assert report_text in agent_response_arg, (
        "Reflection judge should see text emitted alongside tool calls, "
        f"but agent_response was: {agent_response_arg!r}"
    )
    assert trailer_text in agent_response_arg


@pytest.mark.asyncio
async def test_wake_turn_archives_nudge_as_wake_trigger_not_user(ctx, config):
    """A wake turn (task_mode='background_wake') archives the trigger prompt under
    'wake_trigger' role, not 'user', so the web UI doesn't render it as a real
    user message on conversation reload."""
    from decafclaw.archive import read_archive

    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "test"
    ctx.task_mode = "background_wake"
    ctx.conv_id = "test-wake-trigger-archive"

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_llm_response("Noted, job done.")
        history = []
        await run_agent_turn(ctx, "A background job you started has completed.", history)

    all_msgs = read_archive(config, "test-wake-trigger-archive")
    user_msgs = [m for m in all_msgs if m.get("role") == "user"]
    assert not user_msgs, f"Expected no 'user' role records for wake turn, got: {user_msgs}"
    wake_trigger_msgs = [m for m in all_msgs if m.get("role") == "wake_trigger"]
    assert len(wake_trigger_msgs) == 1
    assert "background job" in wake_trigger_msgs[0]["content"].lower()


# -- Public surface contract test ----------------------------------------------


def test_run_agent_turn_public_surface_unchanged():
    """Lock the public signature so future refactors don't drift the
    contract callers depend on (conversation_manager.py, eval/runner,
    etc.)."""
    import inspect
    sig = inspect.signature(run_agent_turn)
    params = sig.parameters

    assert list(params.keys()) == [
        "ctx", "user_message", "history", "archive_text", "attachments",
    ], "Positional/keyword arg order changed"
    assert params["archive_text"].default == "", \
        "archive_text default changed"
    assert params["attachments"].default is None, \
        "attachments default changed"
    assert inspect.iscoroutinefunction(run_agent_turn), \
        "run_agent_turn must remain async"
