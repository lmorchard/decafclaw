"""Tests for sub-agent delegation."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.events import EventBus
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


@pytest.fixture
def ctx(config):
    """ctx fixture with a ConversationManager attached (required for delegation)."""
    from decafclaw.context import Context

    bus = EventBus()
    context = Context(config=config, event_bus=bus)
    context.conv_id = "test-conv"
    context.channel_id = "test-channel"
    context.user_id = "testuser"
    context.manager = ConversationManager(config, bus)
    return context


class TestRunChildTurn:
    @pytest.mark.asyncio
    async def test_basic_child_turn(self, ctx):
        """Child agent runs and returns result text."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="child says hello")

            result = await _run_child_turn(ctx, "say hello")

        assert result == "child says hello"
        mock_run.assert_called_once()

        # Check the child context passed to run_agent_turn
        call_args = mock_run.call_args
        child_ctx = call_args[0][0]
        assert child_ctx.config.agent.max_tool_iterations == 10
        assert child_ctx.config.system_prompt == DEFAULT_CHILD_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_delegate_task_excluded_from_child(self, ctx):
        """The delegate_task tool is excluded from child's allowed tools."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert "delegate_task" not in child_ctx.tools.allowed
        assert "activate_skill" not in child_ctx.tools.allowed
        # Core tools should be inherited
        assert "current_time" in child_ctx.tools.allowed

    @pytest.mark.asyncio
    async def test_inherits_parent_skill_data(self, ctx):
        """Child inherits parent's skill_data."""
        ctx.skills.data = {"vault_base_path": "obsidian/main"}

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.skills.data == {"vault_base_path": "obsidian/main"}

    @pytest.mark.asyncio
    async def test_inherits_parent_extra_tools(self, ctx):
        """Child inherits parent's extra_tools from activated skills."""
        ctx.tools.extra = {"vault_read": lambda ctx, **kw: "data"}

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert "vault_read" in child_ctx.tools.extra
        assert "vault_read" in child_ctx.tools.allowed

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
        """Child that raises returns error text containing the exception message."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock,
                   side_effect=Exception("boom")):
            result = await _run_child_turn(ctx, "bad task")

        # _start_turn catches the exception and forwards it as [error: boom]
        assert "boom" in _text(result)

    @pytest.mark.asyncio
    async def test_cancel_propagation(self, ctx):
        """Parent cancel event is propagated to child context."""
        cancel = asyncio.Event()
        ctx.cancelled = cancel

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.cancelled is cancel

    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self, ctx):
        """Missing manager on parent ctx returns an error."""
        ctx.manager = None

        result = await _run_child_turn(ctx, "task")

        assert "error" in _text(result)
        assert "manager" in _text(result).lower()


    @pytest.mark.asyncio
    async def test_threads_parent_user_id(self, ctx):
        """Child turn inherits parent's user_id."""
        ctx.user_id = "alice"

        seen = {}

        async def fake_run_agent_turn(child_ctx, user_message, history, **kwargs):
            seen["user_id"] = child_ctx.user_id
            return ToolResult(text="done")

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock,
                   side_effect=fake_run_agent_turn):
            await _run_child_turn(ctx, "whatever")

        assert seen["user_id"] == "alice"


class TestToolDelegateTask:
    @pytest.mark.asyncio
    async def test_single_task(self, ctx):
        """Single task returns result directly."""
        with patch("decafclaw.tools.delegate._run_child_turn",
                   new_callable=AsyncMock, return_value="result one"):
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


class TestDelegateRoutingThroughManager:
    @pytest.mark.asyncio
    async def test_delegate_routes_through_manager(self, ctx, monkeypatch):
        """delegate_task routes child turns through ConversationManager.enqueue_turn."""
        seen = []
        orig_enqueue = ctx.manager.enqueue_turn

        async def spy_enqueue(conv_id, *, kind, prompt, **kwargs):
            seen.append({"conv_id": conv_id, "kind": kind, "prompt": prompt[:40]})
            return await orig_enqueue(conv_id, kind=kind, prompt=prompt, **kwargs)

        monkeypatch.setattr(ctx.manager, "enqueue_turn", spy_enqueue)

        async def fake_run_agent_turn(child_ctx, user_message, history, **kwargs):
            return ToolResult(text="done")

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        result = await tool_delegate_task(ctx, "do a thing")

        assert len(seen) == 1
        assert seen[0]["kind"] is TurnKind.CHILD_AGENT
        assert "--child-" in seen[0]["conv_id"]
        assert "done" in _text(result)

    @pytest.mark.asyncio
    async def test_child_conv_id_format(self, ctx, monkeypatch):
        """Child conv_id is based on parent conv_id with a unique suffix."""
        seen_conv_ids = []
        orig_enqueue = ctx.manager.enqueue_turn

        async def spy_enqueue(conv_id, *, kind, prompt, **kwargs):
            seen_conv_ids.append(conv_id)
            return await orig_enqueue(conv_id, kind=kind, prompt=prompt, **kwargs)

        monkeypatch.setattr(ctx.manager, "enqueue_turn", spy_enqueue)
        monkeypatch.setattr(
            "decafclaw.agent.run_agent_turn",
            AsyncMock(return_value=ToolResult(text="ok")),
        )

        await tool_delegate_task(ctx, "task one")
        await tool_delegate_task(ctx, "task two")

        assert len(seen_conv_ids) == 2
        # Both should start with parent conv_id
        assert seen_conv_ids[0].startswith("test-conv--child-")
        assert seen_conv_ids[1].startswith("test-conv--child-")
        # They should be different (random suffix)
        assert seen_conv_ids[0] != seen_conv_ids[1]
