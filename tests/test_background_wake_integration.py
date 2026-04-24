"""End-to-end integration tests for background job wake flow.

Tests the full chain from a background job starting through to the WAKE turn
being run by ConversationManager, covering:
  - Archive contains a background_event record after job exit
  - Inbox receives a 'background' category notification
  - run_agent_turn is called for the wake turn with correct task_mode
  - The wake turn's raw history contains the background_event record
  - BACKGROUND_WAKE_OK suppresses the user-facing message_complete event
  - Wake turn archives nudge under 'wake_trigger' role (not 'user')
  - _finalize_job emits background_event on the conv event stream
"""
import asyncio

import pytest

from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.media import ToolResult
from decafclaw.skills.background.tools import (
    _get_job_manager,
    tool_shell_background_start,
)


async def _yes_approval(*a, **k):
    return {"approved": True}


async def _wait_for_wake_task(manager: ConversationManager, conv_id: str, timeout: float = 5.0) -> None:
    """Poll until the conversation's agent_task is done."""
    state = manager._conversations.get(conv_id)
    if state is None:
        # Turn may not have started yet — yield once and retry.
        await asyncio.sleep(0)
        state = manager._conversations.get(conv_id)
    if state and state.agent_task and not state.agent_task.done():
        await asyncio.wait_for(state.agent_task, timeout=timeout)


@pytest.mark.asyncio
async def test_user_conv_wake_fires_after_background_job_completes(
    ctx, config, monkeypatch
):
    """User-mode conv: start bg job, job exits, wake fires on same conv."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "c-wake-user-1"

    manager = ConversationManager(config, ctx.event_bus)
    ctx.manager = manager

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    captured = []

    async def fake_run_agent_turn(ctx_arg, user_message, history, **kwargs):
        captured.append({
            "task_mode": getattr(ctx_arg, "task_mode", ""),
            "conv_id": ctx_arg.conv_id,
            "user_message": user_message,
            "history": list(history),
        })
        return ToolResult(text="Noted, job done.")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    result = await tool_shell_background_start(ctx, command="true")
    job_manager = _get_job_manager(ctx)
    job = job_manager.get(result.data["job_id"])
    assert job is not None

    # Wait for reader to complete — _finalize_job runs inside the reader.
    await job.reader_task
    # Yield to allow _enqueue_wake's create_task / enqueue_turn to schedule.
    await asyncio.sleep(0)
    # Now wait for the spawned wake agent_task to finish.
    await _wait_for_wake_task(manager, "c-wake-user-1")

    # 1. Archive has background_event record.
    from decafclaw.archive import restore_history
    history = restore_history(config, "c-wake-user-1") or []
    bg_events = [m for m in history if m.get("role") == "background_event"]
    assert len(bg_events) == 1
    assert bg_events[0]["job_id"] == job.job_id
    assert bg_events[0]["status"] == "completed"

    # 2. Inbox has a 'background' notification.
    from decafclaw import notifications
    records, _ = notifications.read_inbox(config)
    bg_notifs = [r for r in records if r.category == "background"]
    assert len(bg_notifs) >= 1

    # 3. run_agent_turn was called for the wake turn.
    wake_calls = [c for c in captured if c["task_mode"] == "background_wake"]
    assert len(wake_calls) == 1

    # 4. Wake turn's raw history includes the background_event record.
    wake_history = wake_calls[0]["history"]
    wake_bg_events = [m for m in wake_history if m.get("role") == "background_event"]
    assert len(wake_bg_events) == 1
    assert wake_bg_events[0]["job_id"] == job.job_id

    await job_manager.cleanup_all()


@pytest.mark.asyncio
async def test_heartbeat_conv_gets_its_own_wake(ctx, config, monkeypatch):
    """Job started from a heartbeat conv fires wake on that heartbeat conv_id."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "heartbeat-T-0"

    manager = ConversationManager(config, ctx.event_bus)
    ctx.manager = manager

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    captured = []

    async def fake_run_agent_turn(ctx_arg, user_message, history, **kwargs):
        captured.append({
            "conv_id": ctx_arg.conv_id,
            "task_mode": getattr(ctx_arg, "task_mode", ""),
        })
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    result = await tool_shell_background_start(ctx, command="true")
    job_manager = _get_job_manager(ctx)
    job = job_manager.get(result.data["job_id"])
    assert job is not None

    await job.reader_task
    await asyncio.sleep(0)
    await _wait_for_wake_task(manager, "heartbeat-T-0")

    wake_calls = [c for c in captured if c["task_mode"] == "background_wake"]
    assert len(wake_calls) == 1
    assert wake_calls[0]["conv_id"] == "heartbeat-T-0"

    await job_manager.cleanup_all()


@pytest.mark.asyncio
async def test_wake_background_wake_ok_suppresses_user_message(
    ctx, config, monkeypatch
):
    """Agent returning BACKGROUND_WAKE_OK causes suppress_user_message=True on message_complete."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "c-suppress-1"

    manager = ConversationManager(config, ctx.event_bus)
    ctx.manager = manager

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    events: list[dict] = []
    manager.subscribe("c-suppress-1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx_arg, user_message, history, **kwargs):
        return ToolResult(text="BACKGROUND_WAKE_OK noted.")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    result = await tool_shell_background_start(ctx, command="true")
    job_manager = _get_job_manager(ctx)
    job = job_manager.get(result.data["job_id"])
    assert job is not None

    await job.reader_task
    await asyncio.sleep(0)
    await _wait_for_wake_task(manager, "c-suppress-1")

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes, "Expected at least one message_complete event"
    assert completes[-1].get("suppress_user_message") is True

    await job_manager.cleanup_all()


@pytest.mark.asyncio
async def test_wake_turn_archives_nudge_as_wake_trigger_not_user(
    ctx, config, monkeypatch
):
    """WAKE turns archive the trigger prompt under 'wake_trigger' role, not 'user',
    so the web UI doesn't render synthetic prompts as user bubbles on reload.

    This test uses the real run_agent_turn but mocks the LLM call so no real
    network traffic is needed.
    """
    from unittest.mock import AsyncMock, patch

    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "c-wake-trigger-role"
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "test"

    manager = ConversationManager(config, ctx.event_bus)
    ctx.manager = manager

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    result = await tool_shell_background_start(ctx, command="true")
    job_manager = _get_job_manager(ctx)
    job = job_manager.get(result.data["job_id"])
    assert job is not None

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = {
            "content": "noted",
            "tool_calls": None,
            "role": "assistant",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        await job.reader_task
        await asyncio.sleep(0)
        await _wait_for_wake_task(manager, "c-wake-trigger-role")

    from decafclaw.archive import read_archive
    all_msgs = read_archive(config, "c-wake-trigger-role")

    # No record should be role 'user' — the wake nudge must be wake_trigger.
    user_msgs = [m for m in all_msgs if m.get("role") == "user"]
    assert not user_msgs, f"Expected no 'user' role records in wake archive, got: {user_msgs}"

    wake_trigger_msgs = [m for m in all_msgs if m.get("role") == "wake_trigger"]
    assert len(wake_trigger_msgs) == 1, (
        f"Expected exactly one 'wake_trigger' record, got: {wake_trigger_msgs}"
    )

    await job_manager.cleanup_all()


@pytest.mark.asyncio
async def test_finalize_job_emits_background_event_on_conv_stream(
    ctx, config, monkeypatch
):
    """_finalize_job publishes a 'background_event' event on the originating conv's
    event stream right when the archive record is appended, so live subscribers
    (web UI, etc.) see the completion immediately without waiting for reload."""
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.conv_id = "c-bg-event-emit"

    manager = ConversationManager(config, ctx.event_bus)
    ctx.manager = manager

    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", _yes_approval)

    events: list[dict] = []
    manager.subscribe("c-bg-event-emit", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx_arg, user_message, history, **kwargs):
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    result = await tool_shell_background_start(ctx, command="true")
    job_manager = _get_job_manager(ctx)
    job = job_manager.get(result.data["job_id"])
    assert job is not None

    await job.reader_task
    await asyncio.sleep(0)
    await _wait_for_wake_task(manager, "c-bg-event-emit")

    bg_events = [e for e in events if e.get("type") == "background_event"]
    assert len(bg_events) == 1, f"Expected one background_event emission, got: {bg_events}"
    assert bg_events[0]["record"]["job_id"] == job.job_id
    assert bg_events[0]["record"]["status"] == "completed"

    await job_manager.cleanup_all()
