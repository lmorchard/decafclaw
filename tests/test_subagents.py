"""Tests for subagent lifecycle tools and scoped vault write (#1113)."""

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.conversation_manager import ConversationManager, ConversationState, TurnKind
from decafclaw.events import EventBus
from decafclaw.media import ToolResult
from decafclaw.tools.delegate import (
    _VAULT_READ_TOOLS,
    _VAULT_WRITE_TOOLS,
    DELEGATE_TOOL_DEFINITIONS,
    run_child_turn,
    tool_delegate_task,
    tool_delegate_tasks,
)
from decafclaw.tools.subagent_tools import (
    SUBAGENT_TOOL_DEFINITIONS,
    subagent_cancel,
    subagent_list,
)


def _text(result):
    """Extract text from a str / ToolResult / (text, data) tuple."""
    if isinstance(result, ToolResult):
        return result.text
    if isinstance(result, tuple):
        return result[0]
    return result


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


# -- Scoped vault write policy via run_child_turn (#1113) -----------------------


class TestScopedVaultWrite:
    @pytest.mark.asyncio
    async def test_default_blocks_all_vault_writes(self, ctx):
        """allow_vault_write defaults to False → no child vault writes."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            await run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        for tool in _VAULT_WRITE_TOOLS:
            assert tool not in child_ctx.tools.allowed, f"{tool} should be excluded by default"

    @pytest.mark.asyncio
    async def test_allow_vault_write_permits_authorship_writes_only(self, ctx):
        """allow_vault_write=True admits vault_write + vault_journal_append;
        every other vault-write tool stays blocked."""
        ctx.tools.extra = {tool: (lambda ctx, **kw: "x") for tool in _VAULT_WRITE_TOOLS}

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            await run_child_turn(ctx, "task", allow_vault_write=True)

        child_ctx = mock_run.call_args[0][0]
        assert "vault_write" in child_ctx.tools.allowed
        assert "vault_journal_append" in child_ctx.tools.allowed
        for tool in _VAULT_WRITE_TOOLS:
            if tool not in {"vault_write", "vault_journal_append"}:
                assert tool not in child_ctx.tools.allowed, f"{tool} should stay blocked"

    @pytest.mark.asyncio
    async def test_allow_vault_write_false_blocks_writes_even_with_read_opt_in(self, ctx):
        """allow_vault_read=True + allow_vault_write=False → reads in, writes out."""
        ctx.tools.extra = {tool: (lambda ctx, **kw: "x") for tool in _VAULT_READ_TOOLS | _VAULT_WRITE_TOOLS}
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            await run_child_turn(ctx, "task", allow_vault_read=True)

        child_ctx = mock_run.call_args[0][0]
        for tool in _VAULT_READ_TOOLS:
            assert tool in child_ctx.tools.allowed
        for tool in _VAULT_WRITE_TOOLS:
            assert tool not in child_ctx.tools.allowed

    @pytest.mark.asyncio
    async def test_flags_combine_retrieval_read_write(self, ctx):
        """allow_vault_retrieval + allow_vault_read + allow_vault_write combine."""
        ctx.tools.extra = {tool: (lambda ctx, **kw: "x") for tool in _VAULT_READ_TOOLS | _VAULT_WRITE_TOOLS}
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            await run_child_turn(
                ctx,
                "task",
                allow_vault_retrieval=True,
                allow_vault_read=True,
                allow_vault_write=True,
            )

        child_ctx = mock_run.call_args[0][0]
        for tool in _VAULT_READ_TOOLS:
            assert tool in child_ctx.tools.allowed
        assert "vault_write" in child_ctx.tools.allowed
        assert "vault_journal_append" in child_ctx.tools.allowed
        ctx_skip = mock_run.call_args[0][0]
        assert ctx_skip.skip_vault_retrieval is False


# -- run_child_turn spawn-only + conv id override (#1113) ------------------------


class TestRunChildTurnSpawn:
    @pytest.mark.asyncio
    async def test_await_child_false_returns_spawn_ack(self, ctx):
        """await_child=False → spawn ack, no turn execution, no timeout wait."""
        ctx.config.agent.child_timeout_sec = 0.1

        async def boom(*args, **kwargs):
            raise AssertionError("child turn must not run in spawn-only mode")

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = boom
            result = await run_child_turn(ctx, "spawn task", await_child=False)

        text, data = result
        assert "spawned" in text
        assert data is None
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_child_conv_id_override_honored(self, ctx, monkeypatch):
        """child_conv_id_override is used instead of generating a new child id."""
        seen_conv_ids = []
        orig_enqueue = ctx.manager.enqueue_turn

        async def spy_enqueue(conv_id, *, kind, prompt, **kwargs):
            seen_conv_ids.append(conv_id)
            fut = orig_enqueue(conv_id, kind=kind, prompt=prompt, **kwargs)
            return fut

        monkeypatch.setattr(ctx.manager, "enqueue_turn", spy_enqueue)

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            custom = "test-conv--child-deadbeef"
            await run_child_turn(ctx, "task", child_conv_id_override=custom)

        assert seen_conv_ids[0] == custom


# -- Subagent management tools ------------------------------------------------


class TestSubagentTools:
    @pytest.mark.asyncio
    async def test_delegate_task_background_requires_task(self, ctx):
        """background=true still requires a task description."""
        result = await tool_delegate_task(ctx, "   ", background=True)
        assert "error" in _text(result)

    @pytest.mark.asyncio
    async def test_delegate_task_background_true_is_spawn_only(self, ctx):
        """background=true → fire-and-forget spawn: ack + child_conv_id, no await."""
        with patch(
            "decafclaw.tools.delegate.run_child_turn",
            new_callable=AsyncMock,
            return_value=("[subagent spawned: test-conv--child-xyz]", None),
        ) as mock_run:
            result = await tool_delegate_task(ctx, "do a thing", background=True)

        assert isinstance(result, ToolResult)
        assert "spawned" in result.text
        assert result.data is not None
        assert str(result.data["child_conv_id"]).startswith("test-conv--child-")
        kwargs = mock_run.call_args.kwargs
        assert kwargs["await_child"] is False
        assert kwargs["return_schema"] is None

    @pytest.mark.asyncio
    async def test_delegate_task_default_awaits_child(self, ctx):
        """background defaults to False → awaited child turn (delegate_task-style)."""
        with patch(
            "decafclaw.tools.delegate.run_child_turn",
            new_callable=AsyncMock,
            return_value=("child answer", None),
        ) as mock_run:
            result = await tool_delegate_task(ctx, "do a thing")

        assert result.text == "child answer"
        kwargs = mock_run.call_args.kwargs
        assert kwargs["await_child"] is True

    def test_delegate_task_tool_schema_exposes_background(self):
        """The LLM-facing delegate_task schema exposes the background flag."""
        (task_def,) = [d for d in DELEGATE_TOOL_DEFINITIONS if d["function"]["name"] == "delegate_task"]
        props = task_def["function"]["parameters"]["properties"]
        assert props["background"]["type"] == "boolean"
        assert "fire-and-forget" in props["background"]["description"].lower()

    @pytest.mark.asyncio
    async def test_delegate_task_awaited_returns_child_answer(self, ctx):
        """background=false (default) with the real manager: the tool blocks
        until the child finishes and its answer lands on ToolResult.text."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="child answer")
            result = await tool_delegate_task(ctx, "do it")

        assert result.text == "child answer"
        mock_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delegate_task_background_true_child_actually_runs(self, ctx):
        """background=true (fire-and-forget) with the real manager: the spawn
        ack returns before the child finishes, but the enqueued turn really
        runs via the manager loop and completes."""
        release = asyncio.Event()

        async def slow_child(*args, **kwargs):
            await release.wait()
            return ToolResult(text="child answer")

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = slow_child
            result = await tool_delegate_task(ctx, "background task", background=True)

            assert "spawned" in result.text
            child_id = result.data["child_conv_id"]
            assert child_id.startswith("test-conv--child-")
            # Task text + running status are visible immediately after the
            # ack (enqueue_turn runs the child setup synchronously).
            subs = subagent_list(ctx).data["subagents"]
            assert subs[0]["status"] == "running"
            assert subs[0]["task"] == "background task"

            child_task = ctx.manager.get_state(child_id).agent_task
            release.set()
            await child_task

        assert mock_run.await_count == 1
        assert subagent_list(ctx).data["subagents"][0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_subagent_list_empty_session(self, ctx):
        result = subagent_list(ctx)
        assert isinstance(result, ToolResult)
        assert "No subagents" in result.text
        assert result.data == {"subagents": []}

    @pytest.mark.asyncio
    async def test_subagent_list_reports_running_child(self, ctx):
        child_id = "test-conv--child-abc123"
        state = ConversationState(conv_id=child_id, busy=True)
        state.agent_task = asyncio.create_task(asyncio.sleep(3600))
        ctx.manager._conversations[child_id] = state
        ctx.manager.set_subagent_task(child_id, "write the frobnicator")

        try:
            result = subagent_list(ctx)
            subs = result.data["subagents"]
            assert len(subs) == 1
            assert subs[0]["child_conv_id"] == child_id
            assert subs[0]["status"] == "running"
            assert subs[0]["task"] == "write the frobnicator"
        finally:
            state.agent_task.cancel()
            with suppress(asyncio.CancelledError):
                await state.agent_task

    @pytest.mark.asyncio
    async def test_subagent_list_reports_queued_child(self, ctx):
        """Enqueued but not yet dispatched (turn pending in the per-conv
        queue, busy still False) reports "queued", not "completed"."""
        child_id = "test-conv--child-abc123"
        state = ConversationState(conv_id=child_id)
        state.inmemory_turn_data["turn-1"] = {
            "future": asyncio.get_running_loop().create_future(),
        }
        ctx.manager._conversations[child_id] = state
        ctx.manager.set_subagent_task(child_id, "queued job")

        result = subagent_list(ctx)
        subs = result.data["subagents"]
        assert len(subs) == 1
        assert subs[0]["status"] == "queued"
        assert subs[0]["task"] == "queued job"

    @pytest.mark.asyncio
    async def test_subagent_list_reports_completed_child(self, ctx):
        child_id = "test-conv--child-abc123"
        ctx.manager._conversations[child_id] = ConversationState(conv_id=child_id)
        ctx.manager.set_subagent_task(child_id, "write the frobnicator")

        result = subagent_list(ctx)
        assert isinstance(result, ToolResult)
        subs = result.data["subagents"]
        assert len(subs) == 1
        assert subs[0]["child_conv_id"] == child_id
        assert subs[0]["status"] == "completed"
        assert subs[0]["task"] == "write the frobnicator"

    @pytest.mark.asyncio
    async def test_subagent_cancel_aborts_child_turn(self, ctx):
        child_id = "test-conv--child-abc123"
        ctx.manager._conversations[child_id] = ConversationState(conv_id=child_id)

        with patch.object(ctx.manager, "cancel_turn", new_callable=AsyncMock) as mock_cancel:
            result = await subagent_cancel(ctx, child_id)

        mock_cancel.assert_awaited_once_with(child_id)
        assert child_id in _text(result)
        assert "cancelled" in _text(result)

    @pytest.mark.asyncio
    async def test_subagent_cancel_rejects_non_child_conversation(self, ctx):
        """subagent_cancel only targets children of the CALLER's conversation:
        neither the caller itself nor a tracked conversation that is another
        conversation's child."""
        foreign_child = "other-conv--child-deadbeef"
        ctx.manager._conversations[foreign_child] = ConversationState(conv_id=foreign_child)

        for target in ("test-conv", foreign_child):
            with patch.object(ctx.manager, "cancel_turn", new_callable=AsyncMock) as mock_cancel:
                result = await subagent_cancel(ctx, target)
            assert "error" in _text(result), target
            mock_cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_child_turn_excludes_subagent_tools(self, ctx):
        """Children never see delegation or the subagent lifecycle tools — the
        only spawn path (delegate_task) is excluded so a child spawning
        grandchildren would bypass the delegate_task recursion boundary, and
        subagent_cancel must never target a conversation the child doesn't own."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            await run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        for tool in ("delegate_task", "subagent_list", "subagent_cancel"):
            assert tool not in child_ctx.tools.allowed, f"children must not see {tool}"


# -- delegate_task / delegate_tasks threading (#1113) --------------------------


class TestDelegateFlagThreading:
    @pytest.mark.asyncio
    async def test_delegate_task_threads_allow_vault_write(self, ctx):
        seen = {}

        async def fake_run(parent_ctx, task, model="", max_iterations=0, **kwargs):
            seen["allow_vault_write"] = kwargs.get("allow_vault_write")
            return ("ok", None)

        with patch("decafclaw.tools.delegate.run_child_turn", side_effect=fake_run):
            result = await tool_delegate_task(ctx, "task", allow_vault_write=True)

        assert _text(result) == "ok"
        assert seen["allow_vault_write"] is True

    @pytest.mark.asyncio
    async def test_delegate_tasks_threads_allow_vault_write(self, ctx):
        async def fake_run(parent_ctx, task, **kwargs):
            return (f"ok {task}", None)

        with patch("decafclaw.tools.delegate.run_child_turn", side_effect=fake_run):
            result = await tool_delegate_tasks(ctx, ["a", "b"], allow_vault_write=True)

        assert result.data["summary"]["ok"] == 2
        assert all(r["ok"] for r in result.data["results"])
