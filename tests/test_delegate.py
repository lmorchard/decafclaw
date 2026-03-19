"""Tests for sub-agent delegation."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.media import ToolResult
from decafclaw.tools.delegate import (
    DEFAULT_CHILD_SYSTEM_PROMPT,
    _run_child_turn,
    tool_delegate_task,
)


def _text(result):
    """Extract text from str or ToolResult."""
    return result.text if isinstance(result, ToolResult) else result


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

            result = await _run_child_turn(ctx, "say hello")

        assert result == "child says hello"
        mock_run.assert_called_once()

        # Check the child context
        call_args = mock_run.call_args
        child_ctx = call_args[0][0]
        assert child_ctx.config.agent.max_tool_iterations == 10
        assert child_ctx.config.system_prompt == DEFAULT_CHILD_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_delegate_task_excluded_from_child(self, ctx):
        """The delegate_task tool is excluded from child's allowed tools."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            from decafclaw.media import ToolResult
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert "delegate_task" not in child_ctx.allowed_tools
        assert "activate_skill" not in child_ctx.allowed_tools
        # Core tools should be inherited
        assert "memory_search" in child_ctx.allowed_tools

    @pytest.mark.asyncio
    async def test_inherits_parent_skill_data(self, ctx):
        """Child inherits parent's skill_data."""
        ctx.skill_data = {"vault_base_path": "obsidian/main"}

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            from decafclaw.media import ToolResult
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.skill_data == {"vault_base_path": "obsidian/main"}

    @pytest.mark.asyncio
    async def test_inherits_parent_extra_tools(self, ctx):
        """Child inherits parent's extra_tools from activated skills."""
        ctx.extra_tools = {"vault_read": lambda ctx, **kw: "data"}

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            from decafclaw.media import ToolResult
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert "vault_read" in child_ctx.extra_tools
        assert "vault_read" in child_ctx.allowed_tools

    @pytest.mark.asyncio
    async def test_timeout(self, ctx):
        """Child that exceeds timeout returns error."""
        async def slow_turn(*args, **kwargs):
            await asyncio.sleep(10)

        ctx.config.agent.child_timeout_sec = 0.1

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock, side_effect=slow_turn):
            result = await _run_child_turn(ctx, "slow task")

        assert "timed out" in _text(result)

    @pytest.mark.asyncio
    async def test_child_error(self, ctx):
        """Child that raises returns error text."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock, side_effect=Exception("boom")):
            result = await _run_child_turn(ctx, "bad task")

        assert "subtask failed" in _text(result)
        assert "boom" in _text(result)

    @pytest.mark.asyncio
    async def test_cancel_propagation(self, ctx):
        """Parent cancel event is propagated to child context."""
        cancel = asyncio.Event()
        ctx.cancelled = cancel

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            from decafclaw.media import ToolResult
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.cancelled is cancel


class TestToolDelegateTask:
    @pytest.mark.asyncio
    async def test_single_task(self, ctx):
        """Single task returns result directly."""
        with patch("decafclaw.tools.delegate._run_child_turn", new_callable=AsyncMock, return_value="result one"):
            result = await tool_delegate_task(ctx, "do thing")

        assert result == "result one"

    @pytest.mark.asyncio
    async def test_empty_task(self, ctx):
        """Empty task returns error."""
        result = await tool_delegate_task(ctx, "")
        assert "error" in _text(result)

    @pytest.mark.asyncio
    async def test_whitespace_task(self, ctx):
        """Whitespace-only task returns error."""
        result = await tool_delegate_task(ctx, "   ")
        assert "error" in _text(result)
