"""Tests for day-one notification producers (heartbeat, schedule, background, compaction, reflection)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw import notifications as notifs
from decafclaw.events import EventBus
from decafclaw.media import ToolResult

# -- Heartbeat ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_cycle_emits_notification(config):
    """run_heartbeat_cycle appends an inbox notification summarizing the cycle."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.heartbeat import run_heartbeat_cycle

    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## A\n\nTask A.\n\n## B\n\nTask B.\n")

    bus = EventBus()
    manager = ConversationManager(config, bus)
    mock_agent = AsyncMock(side_effect=[
        ToolResult(text="HEARTBEAT_OK"),
        ToolResult(text="Something broke"),  # not OK
    ])
    with patch("decafclaw.agent.run_agent_turn", mock_agent):
        await run_heartbeat_cycle(config, bus, manager)

    records, _ = notifs.read_inbox(config)
    assert len(records) == 1
    rec = records[0]
    assert rec.category == "heartbeat"
    assert "1 OK, 1 alert(s)" in rec.body
    assert rec.priority == "high"


@pytest.mark.asyncio
async def test_heartbeat_cycle_empty_no_notification(config):
    """Empty cycle (no sections) does not emit a notification."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.heartbeat import run_heartbeat_cycle

    bus = EventBus()
    manager = ConversationManager(config, bus)
    await run_heartbeat_cycle(config, bus, manager)
    records, _ = notifs.read_inbox(config)
    assert records == []


# -- Scheduled task -----------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduled_task_emits_notification(config, tmp_path):
    """run_schedule_task emits an inbox notification on completion."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.schedules import ScheduleTask, run_schedule_task

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    task_file = skill_dir / "task.md"
    task_file.write_text("Body")

    task = ScheduleTask(
        name="test-task", schedule="* * * * *", channel="", enabled=True,
        body="Do the thing", source="admin", path=task_file,
        model="", allowed_tools=[], required_skills=[], shell_patterns=[],
    )

    bus = EventBus()
    manager = ConversationManager(config, bus)
    with patch("decafclaw.agent.run_agent_turn",
               AsyncMock(return_value=ToolResult(text="HEARTBEAT_OK: done"))):
        await run_schedule_task(config, bus, manager, task)

    records, _ = notifs.read_inbox(config)
    assert len(records) == 1
    assert records[0].category == "schedule"
    assert "test-task" in records[0].title
    # conv_id must be populated so the web UI can navigate to the run
    assert records[0].conv_id
    assert records[0].conv_id.startswith("schedule-test-task-")


@pytest.mark.asyncio
async def test_scheduled_task_failure_emits_high_priority(config, tmp_path):
    """A failing scheduled task emits a high-priority alert."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.schedules import ScheduleTask, run_schedule_task

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    task_file = skill_dir / "task.md"
    task_file.write_text("Body")

    task = ScheduleTask(
        name="broken", schedule="* * * * *", channel="", enabled=True,
        body="Do the thing", source="admin", path=task_file,
        model="", allowed_tools=[], required_skills=[], shell_patterns=[],
    )

    bus = EventBus()
    manager = ConversationManager(config, bus)
    with patch("decafclaw.agent.run_agent_turn",
               AsyncMock(side_effect=RuntimeError("boom"))):
        await run_schedule_task(config, bus, manager, task)

    records, _ = notifs.read_inbox(config)
    assert len(records) == 1
    assert records[0].priority == "high"
    assert "broken" in records[0].title


# -- Background job -----------------------------------------------------------


@pytest.mark.asyncio
async def test_background_job_exit_emits_notification(config):
    """Background-job exit appends a completion notification."""
    from decafclaw.skills.background.tools import BackgroundJobManager

    config.workspace_path.mkdir(parents=True, exist_ok=True)
    manager = BackgroundJobManager()
    job = await manager.start(
        "true",  # quick zero-exit
        cwd=str(config.workspace_path),
        config=config,
        conv_id="conv-xyz",
    )
    # Wait for the reader task to complete
    assert job.reader_task is not None
    await job.reader_task

    records, _ = notifs.read_inbox(config)
    assert len(records) == 1
    rec = records[0]
    assert rec.category == "background"
    assert rec.conv_id == "conv-xyz"
    assert "completed" in rec.title.lower()
    assert rec.priority == "normal"


@pytest.mark.asyncio
async def test_background_job_failure_emits_high_priority(config):
    """Non-zero exit produces a high-priority failure notification."""
    from decafclaw.skills.background.tools import BackgroundJobManager

    config.workspace_path.mkdir(parents=True, exist_ok=True)
    manager = BackgroundJobManager()
    job = await manager.start(
        "false",  # exit code 1
        cwd=str(config.workspace_path),
        config=config,
        conv_id="conv-xyz",
    )
    assert job.reader_task is not None
    await job.reader_task

    records, _ = notifs.read_inbox(config)
    assert len(records) == 1
    assert records[0].priority == "high"
    assert "failed" in records[0].title.lower()


