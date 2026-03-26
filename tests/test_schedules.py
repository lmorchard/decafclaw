"""Tests for scheduled task parsing, discovery, and execution."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.events import EventBus
from decafclaw.schedules import (
    ScheduleTask,
    discover_schedules,
    is_due,
    parse_schedule_file,
    read_last_run,
    run_schedule_task,
    run_schedule_timer,
    write_last_run,
)

# -- Parsing -------------------------------------------------------------------


class TestParseScheduleFile:
    def test_basic_parse(self, tmp_path):
        f = tmp_path / "daily-summary.md"
        f.write_text(
            "---\n"
            'schedule: "0 9 * * 1-5"\n'
            'channel: "#reports"\n'
            "---\n\n"
            "Summarize the day.\n"
        )
        task = parse_schedule_file(f)
        assert task is not None
        assert task.name == "daily-summary"
        assert task.schedule == "0 9 * * 1-5"
        assert task.channel == "#reports"
        assert task.enabled is True
        assert task.effort == "default"
        assert "Summarize the day." in task.body

    def test_all_frontmatter_fields(self, tmp_path):
        f = tmp_path / "check.md"
        f.write_text(
            "---\n"
            'schedule: "*/15 * * * *"\n'
            "enabled: false\n"
            "effort: fast\n"
            "allowed-tools:\n"
            "  - workspace_read\n"
            "  - workspace_list\n"
            "required-skills:\n"
            "  - tabstack\n"
            "---\n\n"
            "Check things.\n"
        )
        task = parse_schedule_file(f)
        assert task is not None
        assert task.enabled is False
        assert task.effort == "fast"
        assert task.allowed_tools == ["workspace_read", "workspace_list"]
        assert task.required_skills == ["tabstack"]

    def test_missing_schedule_returns_none(self, tmp_path):
        f = tmp_path / "bad.md"
        f.write_text("---\nchannel: '#foo'\n---\n\nNo schedule.\n")
        assert parse_schedule_file(f) is None

    def test_invalid_cron_returns_none(self, tmp_path):
        f = tmp_path / "bad-cron.md"
        f.write_text("---\nschedule: 'not a cron'\n---\n\nBad cron.\n")
        assert parse_schedule_file(f) is None

    def test_no_frontmatter_returns_none(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("Just a plain markdown file.\n")
        assert parse_schedule_file(f) is None

    def test_defaults(self, tmp_path):
        f = tmp_path / "minimal.md"
        f.write_text("---\nschedule: '* * * * *'\n---\nDo it.\n")
        task = parse_schedule_file(f)
        assert task is not None
        assert task.channel == ""
        assert task.enabled is True
        assert task.effort == "default"
        assert task.allowed_tools == []
        assert task.required_skills == []


# -- Discovery ----------------------------------------------------------------


class TestDiscoverSchedules:
    def test_discovers_from_both_dirs(self, config):
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "admin-task.md").write_text(
            "---\nschedule: '0 9 * * *'\n---\nAdmin task.\n"
        )
        workspace = config.workspace_path / "schedules"
        workspace.mkdir(parents=True)
        (workspace / "agent-task.md").write_text(
            "---\nschedule: '0 12 * * *'\n---\nAgent task.\n"
        )
        tasks = discover_schedules(config)
        names = {t.name for t in tasks}
        assert "admin-task" in names
        assert "agent-task" in names

    def test_admin_takes_precedence_on_collision(self, config):
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "task.md").write_text(
            "---\nschedule: '0 9 * * *'\n---\nAdmin version.\n"
        )
        workspace = config.workspace_path / "schedules"
        workspace.mkdir(parents=True)
        (workspace / "task.md").write_text(
            "---\nschedule: '0 12 * * *'\n---\nWorkspace version.\n"
        )
        tasks = discover_schedules(config)
        matching = [t for t in tasks if t.name == "task"]
        assert len(matching) == 1
        assert matching[0].source == "admin"

    def test_empty_dirs_still_finds_bundled(self, config):
        """With no file-based schedules, bundled skills with schedules still appear."""
        tasks = discover_schedules(config)
        sources = {t.source for t in tasks}
        # Only bundled skills should appear (no admin/workspace file-based)
        assert sources <= {"bundled"}

    def test_skips_invalid_files(self, config):
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "good.md").write_text(
            "---\nschedule: '0 9 * * *'\n---\nGood.\n"
        )
        (admin / "bad.md").write_text("No frontmatter here.\n")
        tasks = discover_schedules(config)
        names = {t.name for t in tasks}
        assert "good" in names
        assert "bad" not in names

    def test_discovers_bundled_skill_schedules(self, config):
        """Bundled skills with schedule field appear in discover_schedules."""
        tasks = discover_schedules(config)
        names = {t.name for t in tasks}
        # The real bundled dream skill has a schedule in its SKILL.md
        assert "dream" in names
        task = [t for t in tasks if t.name == "dream"][0]
        assert task.source == "bundled"

    def test_ignores_workspace_skill_schedules(self, config):
        """Workspace skills with schedule field are ignored (trust boundary)."""
        skill_dir = config.workspace_path / "skills" / "sneaky"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: sneaky\ndescription: Sneaky\n"
            "schedule: '* * * * *'\n---\nI should not run.\n"
        )
        tasks = discover_schedules(config)
        names = {t.name for t in tasks}
        assert "sneaky" not in names

    def test_file_schedule_overrides_skill_schedule(self, config):
        """File-based schedules take precedence over skill frontmatter."""
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "dream.md").write_text(
            "---\nschedule: '0 3 * * *'\n---\nFile version.\n"
        )
        tasks = discover_schedules(config)
        dream = [t for t in tasks if t.name == "dream"][0]
        assert dream.schedule == "0 3 * * *"
        assert "File version" in dream.body

    def test_admin_skill_schedules_discovered(self, config):
        """Admin-level skills with schedule are discovered from disk."""
        skill_dir = config.agent_path / "skills" / "admin-job"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: admin-job\ndescription: Admin scheduled job\n"
            "schedule: '0 6 * * *'\neffort: fast\n---\nDo admin things.\n"
        )
        tasks = discover_schedules(config)
        names = {t.name for t in tasks}
        assert "admin-job" in names
        task = [t for t in tasks if t.name == "admin-job"][0]
        assert task.source == "admin"
        assert task.effort == "fast"

    def test_skill_allowed_tools_propagated(self, config):
        """Skill allowed-tools are propagated to the ScheduleTask."""
        skill_dir = config.agent_path / "skills" / "ingest-job"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: ingest-job\ndescription: Ingest job\n"
            "schedule: '0 */4 * * *'\n"
            "allowed-tools: shell, wiki_read, wiki_write\n"
            "---\nRun the ingest.\n"
        )
        tasks = discover_schedules(config)
        task = [t for t in tasks if t.name == "ingest-job"][0]
        assert task.allowed_tools == ["shell", "wiki_read", "wiki_write"]

    def test_disabled_skill_schedule(self, config):
        """Skills with enabled: false are discovered but marked disabled."""
        skill_dir = config.agent_path / "skills" / "paused-job"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: paused-job\ndescription: Paused\n"
            "schedule: '0 * * * *'\nenabled: false\n---\nPaused.\n"
        )
        tasks = discover_schedules(config)
        task = [t for t in tasks if t.name == "paused-job"][0]
        assert task.enabled is False


# -- Last-run tracking --------------------------------------------------------


class TestLastRun:
    def test_read_no_file(self, config):
        assert read_last_run(config, "nonexistent") == 0

    def test_write_and_read(self, config):
        write_last_run(config, "my-task", 1700000000.0)
        assert read_last_run(config, "my-task") == 1700000000.0

    def test_write_defaults_to_now(self, config):
        before = time.time()
        write_last_run(config, "now-task")
        after = time.time()
        ts = read_last_run(config, "now-task")
        assert before <= ts <= after


class TestIsDue:
    def test_never_run_is_due(self, config):
        task = ScheduleTask(
            name="test", schedule="* * * * *", body="test",
            source="admin", path=Path("/fake"),
        )
        assert is_due(config, task) is True

    def test_recently_run_not_due(self, config):
        task = ScheduleTask(
            name="test", schedule="0 9 * * *", body="test",
            source="admin", path=Path("/fake"),
        )
        write_last_run(config, "test", time.time())
        assert is_due(config, task) is False

    def test_overdue_is_due(self, config):
        task = ScheduleTask(
            name="test", schedule="* * * * *", body="test",
            source="admin", path=Path("/fake"),
        )
        # Ran 2 minutes ago, every-minute cron → due
        write_last_run(config, "test", time.time() - 120)
        assert is_due(config, task) is True

    def test_not_due_in_non_utc_timezone(self, config):
        """is_due must work correctly regardless of local timezone.

        Regression: croniter.get_next(float) uses calendar.timegm which
        treats naive datetimes as UTC. If the base datetime comes from
        datetime.fromtimestamp() (naive local time), the epoch conversion
        is wrong by the timezone offset, causing tasks to appear due
        immediately after running on non-UTC servers.
        """
        import os
        from unittest.mock import patch as _patch

        task = ScheduleTask(
            name="tz-test", schedule="0 */3 * * *", body="test",
            source="admin", path=Path("/fake"),
        )

        # Simulate: task just ran at 21:49 UTC on a US/Eastern server
        # The every-3-hours cron (0,3,6,9,12,15,18,21) means next fire
        # after 21:49 is 0:00 next day — so at 21:50 it should NOT be due.
        from datetime import datetime, timezone
        ran_at = datetime(2026, 3, 24, 21, 49, 25, tzinfo=timezone.utc)
        ran_epoch = ran_at.timestamp()
        check_at = datetime(2026, 3, 24, 21, 50, 0, tzinfo=timezone.utc)
        check_epoch = check_at.timestamp()

        write_last_run(config, "tz-test", ran_epoch)

        old_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "US/Eastern"
            time.tzset()
            with _patch("decafclaw.schedules.time") as mock_time:
                mock_time.time.return_value = check_epoch
                assert is_due(config, task) is False, (
                    "Task should not be due 1 minute after running — "
                    "timezone offset caused get_next(float) to return wrong epoch"
                )
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()


# -- Task execution -----------------------------------------------------------


class TestRunScheduleTask:
    @pytest.mark.asyncio
    async def test_runs_agent_turn(self, config):
        task = ScheduleTask(
            name="test-task", schedule="* * * * *",
            body="Do the thing.", source="admin", path=Path("/fake"),
            effort="fast",
        )
        mock_response = MagicMock()
        mock_response.text = "Done."

        with patch("decafclaw.agent.run_agent_turn",
                    new_callable=AsyncMock, return_value=mock_response):
            result = await run_schedule_task(config, EventBus(), task)

        assert result["response"] == "Done."
        assert result["is_ok"] is False
        assert result["task_name"] == "test-task"
        assert result["channel"] == ""

    @pytest.mark.asyncio
    async def test_heartbeat_ok_detected(self, config):
        task = ScheduleTask(
            name="test-task", schedule="* * * * *",
            body="Check.", source="admin", path=Path("/fake"),
        )
        mock_response = MagicMock()
        mock_response.text = "HEARTBEAT_OK — nothing to report."

        with patch("decafclaw.agent.run_agent_turn",
                    new_callable=AsyncMock, return_value=mock_response):
            result = await run_schedule_task(config, EventBus(), task)

        assert result["is_ok"] is True

    @pytest.mark.asyncio
    async def test_handles_error(self, config):
        task = ScheduleTask(
            name="failing-task", schedule="* * * * *",
            body="Fail.", source="admin", path=Path("/fake"),
        )
        with patch("decafclaw.agent.run_agent_turn",
                    new_callable=AsyncMock, side_effect=Exception("boom")):
            result = await run_schedule_task(config, EventBus(), task)

        assert result["is_ok"] is False
        assert "boom" in result["response"]

    @pytest.mark.asyncio
    async def test_channel_in_result(self, config):
        task = ScheduleTask(
            name="test", schedule="* * * * *",
            body="Report.", source="admin", path=Path("/fake"),
            channel="#reports",
        )
        mock_response = MagicMock()
        mock_response.text = "Report done."

        with patch("decafclaw.agent.run_agent_turn",
                    new_callable=AsyncMock, return_value=mock_response):
            result = await run_schedule_task(config, EventBus(), task)

        assert result["channel"] == "#reports"


# -- Timer loop ----------------------------------------------------------------


class TestRunScheduleTimer:
    @pytest.mark.asyncio
    async def test_executes_due_task(self, config):
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "test.md").write_text(
            "---\nschedule: '* * * * *'\n---\nDo the thing.\n"
        )

        shutdown = asyncio.Event()
        executed = []

        async def fake_run(cfg, bus, task):
            executed.append(task.name)
            return {"task_name": task.name, "channel": "", "response": "ok",
                    "is_ok": True, "context_id": None}

        with patch("decafclaw.schedules.run_schedule_task", side_effect=fake_run):
            async def stop_soon():
                await asyncio.sleep(0.05)
                shutdown.set()
            asyncio.create_task(stop_soon())
            await run_schedule_timer(config, EventBus(), shutdown,
                                     poll_interval=0.02)

        assert "test" in executed

    @pytest.mark.asyncio
    async def test_skips_disabled_task(self, config):
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "disabled.md").write_text(
            "---\nschedule: '* * * * *'\nenabled: false\n---\nSkip me.\n"
        )

        shutdown = asyncio.Event()
        executed = []

        async def fake_run(cfg, bus, task):
            executed.append(task.name)
            return {"task_name": task.name, "channel": "", "response": "ok",
                    "is_ok": True, "context_id": None}

        with patch("decafclaw.schedules.run_schedule_task", side_effect=fake_run):
            async def stop_soon():
                await asyncio.sleep(0.05)
                shutdown.set()
            asyncio.create_task(stop_soon())
            await run_schedule_timer(config, EventBus(), shutdown,
                                     poll_interval=0.02)

        assert "disabled" not in executed

    @pytest.mark.asyncio
    async def test_no_tasks_no_crash(self, config):
        """Timer runs fine with no schedule files."""
        shutdown = asyncio.Event()

        async def stop_soon():
            await asyncio.sleep(0.05)
            shutdown.set()
        asyncio.create_task(stop_soon())
        await run_schedule_timer(config, EventBus(), shutdown,
                                 poll_interval=0.02)
