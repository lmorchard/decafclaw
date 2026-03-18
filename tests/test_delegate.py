"""Tests for sub-agent delegation."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.tools.delegate import (
    DEFAULT_CHILD_SYSTEM_PROMPT,
    _run_child_turn,
    tool_delegate,
)


def _mock_llm_response(content="child result"):
    return {
        "content": content,
        "tool_calls": None,
        "role": "assistant",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class TestRunChildTurn:
    @pytest.mark.asyncio
    async def test_basic_child_turn(self, ctx):
        """Child agent runs and returns result text."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            from decafclaw.media import ToolResult
            mock_run.return_value = ToolResult(text="child says hello")

            result = await _run_child_turn(ctx, "say hello", ["memory_search"])

        assert result == "child says hello"
        mock_run.assert_called_once()

        # Check the child context
        call_args = mock_run.call_args
        child_ctx = call_args[0][0]
        assert child_ctx.allowed_tools == {"memory_search"}
        assert child_ctx.config.max_tool_iterations == 10
        assert child_ctx.config.system_prompt == DEFAULT_CHILD_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_custom_system_prompt(self, ctx):
        """Child uses custom system prompt when provided."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            from decafclaw.media import ToolResult
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task", ["memory_search"], system_prompt="Be a pirate.")

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.config.system_prompt == "Be a pirate."

    @pytest.mark.asyncio
    async def test_delegate_excluded_from_child(self, ctx):
        """The delegate tool is excluded from child's allowed tools."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            from decafclaw.media import ToolResult
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task", ["memory_search", "delegate"])

        child_ctx = mock_run.call_args[0][0]
        assert "delegate" not in child_ctx.allowed_tools
        assert "memory_search" in child_ctx.allowed_tools

    @pytest.mark.asyncio
    async def test_timeout(self, ctx):
        """Child that exceeds timeout returns error."""
        async def slow_turn(*args, **kwargs):
            await asyncio.sleep(10)

        ctx.config.child_timeout_sec = 0.1

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock, side_effect=slow_turn):
            result = await _run_child_turn(ctx, "slow task", [])

        assert "timed out" in result

    @pytest.mark.asyncio
    async def test_child_error(self, ctx):
        """Child that raises returns error text."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock, side_effect=Exception("boom")):
            result = await _run_child_turn(ctx, "bad task", [])

        assert "subtask failed" in result
        assert "boom" in result

    @pytest.mark.asyncio
    async def test_cancel_propagation(self, ctx):
        """Parent cancel event is propagated to child context."""
        cancel = asyncio.Event()
        ctx.cancelled = cancel

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            from decafclaw.media import ToolResult
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task", [])

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.cancelled is cancel


class TestToolDelegate:
    @pytest.mark.asyncio
    async def test_single_task(self, ctx):
        """Single task returns result directly."""
        with patch("decafclaw.tools.delegate._run_child_turn", new_callable=AsyncMock, return_value="result one"):
            result = await tool_delegate(ctx, [{"task": "do thing", "tools": ["shell"]}])

        assert result == "result one"

    @pytest.mark.asyncio
    async def test_parallel_tasks(self, ctx):
        """Multiple tasks run concurrently and return labeled results."""
        call_count = 0

        async def mock_child(ctx, task, tools, system_prompt=None):
            nonlocal call_count
            call_count += 1
            return f"result for: {task}"

        with patch("decafclaw.tools.delegate._run_child_turn", side_effect=mock_child):
            result = await tool_delegate(ctx, [
                {"task": "task A", "tools": ["shell"]},
                {"task": "task B", "tools": ["memory_search"]},
            ])

        assert call_count == 2
        assert "Task 1:" in result
        assert "Task 2:" in result
        assert "result for: task A" in result
        assert "result for: task B" in result

    @pytest.mark.asyncio
    async def test_empty_tasks(self, ctx):
        """Empty task list returns error."""
        result = await tool_delegate(ctx, [])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_task(self, ctx):
        """Task missing required fields returns error."""
        result = await tool_delegate(ctx, [{"task": "no tools field"}])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_parallel_partial_failure(self, ctx):
        """One failing task doesn't prevent other results."""
        async def mock_child(ctx, task, tools, system_prompt=None):
            if "fail" in task:
                raise Exception("kaboom")
            return f"ok: {task}"

        with patch("decafclaw.tools.delegate._run_child_turn", side_effect=mock_child):
            result = await tool_delegate(ctx, [
                {"task": "good task", "tools": []},
                {"task": "fail task", "tools": []},
            ])

        assert "ok: good task" in result
        assert "error" in result
        assert "kaboom" in result
