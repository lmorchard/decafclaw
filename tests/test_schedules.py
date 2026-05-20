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
        assert task.model == ""
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
        assert task.model == "fast"
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
        assert task.model == ""
        assert task.allowed_tools == []
        assert task.required_skills == []
        assert task.email_recipients == []

    def test_email_recipients_list(self, tmp_path):
        f = tmp_path / "emailer.md"
        f.write_text(
            "---\n"
            "schedule: '0 9 * * 1'\n"
            "allowed-tools: send_email\n"
            "email-recipients:\n"
            "  - digest@example.com\n"
            "  - '@team.example.com'\n"
            "---\n"
            "Send the weekly digest.\n"
        )
        task = parse_schedule_file(f)
        assert task is not None
        assert task.email_recipients == [
            "digest@example.com", "@team.example.com",
        ]

    def test_email_recipients_scalar_coerced_to_list(self, tmp_path):
        f = tmp_path / "single.md"
        f.write_text(
            "---\n"
            "schedule: '0 9 * * 1'\n"
            "email-recipients: lone@example.com\n"
            "---\n"
            "One recipient.\n"
        )
        task = parse_schedule_file(f)
        assert task is not None
        assert task.email_recipients == ["lone@example.com"]


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
        """With no file-based schedules, bundled skills with SCHEDULE.md still appear."""
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

    def test_bundled_skill_schedule_md_discovered(self, config):
        """Bundled skills with SCHEDULE.md appear in discover_schedules."""
        tasks = discover_schedules(config)
        names = {t.name for t in tasks}
        # dream has a SCHEDULE.md sidecar
        assert "dream" in names
        task = [t for t in tasks if t.name == "dream"][0]
        assert task.source == "bundled"

    def test_ignores_workspace_skill_schedule_md(self, config):
        """Workspace skills with SCHEDULE.md are ignored (trust boundary)."""
        skill_dir = config.workspace_path / "skills" / "sneaky"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: sneaky\ndescription: Sneaky\n---\nI should not run.\n"
        )
        (skill_dir / "SCHEDULE.md").write_text(
            "---\nschedule: '* * * * *'\n---\nI should not run.\n"
        )
        tasks = discover_schedules(config)
        names = {t.name for t in tasks}
        assert "sneaky" not in names

    def test_admin_standalone_overrides_skill_schedule_md(self, config):
        """File-based admin schedules take precedence over skill SCHEDULE.md."""
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "dream.md").write_text(
            "---\nschedule: '0 3 * * *'\n---\nFile version.\n"
        )
        tasks = discover_schedules(config)
        dream = [t for t in tasks if t.name == "dream"][0]
        assert dream.schedule == "0 3 * * *"
        assert "File version" in dream.body
        assert dream.source == "admin"

    def test_admin_skill_schedule_md_discovered(self, config):
        """Admin-level skills with SCHEDULE.md are discovered from disk."""
        skill_dir = config.agent_path / "skills" / "admin-job"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: admin-job\ndescription: Admin scheduled job\n---\nDo admin things.\n"
        )
        (skill_dir / "SCHEDULE.md").write_text(
            "---\nschedule: '0 6 * * *'\neffort: fast\n---\nDo admin things.\n"
        )
        tasks = discover_schedules(config)
        names = {t.name for t in tasks}
        assert "admin-job" in names
        task = [t for t in tasks if t.name == "admin-job"][0]
        assert task.source == "admin"
        assert task.model == "fast"

    def test_schedule_md_allowed_tools_propagated(self, config):
        """SCHEDULE.md allowed-tools are propagated to the ScheduleTask."""
        skill_dir = config.agent_path / "skills" / "ingest-job"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: ingest-job\ndescription: Ingest job\n---\nRun the ingest.\n"
        )
        (skill_dir / "SCHEDULE.md").write_text(
            "---\nschedule: '0 */4 * * *'\n"
            "allowed-tools: shell, wiki_read, wiki_write\n"
            "---\nRun the ingest.\n"
        )
        tasks = discover_schedules(config)
        task = [t for t in tasks if t.name == "ingest-job"][0]
        assert task.allowed_tools == ["shell", "wiki_read", "wiki_write"]

    def test_disabled_schedule_md(self, config):
        """SCHEDULE.md with enabled: false is discovered but marked disabled."""
        skill_dir = config.agent_path / "skills" / "paused-job"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: paused-job\ndescription: Paused\n---\nPaused.\n"
        )
        (skill_dir / "SCHEDULE.md").write_text(
            "---\nschedule: '0 * * * *'\nenabled: false\n---\nPaused.\n"
        )
        tasks = discover_schedules(config)
        task = [t for t in tasks if t.name == "paused-job"][0]
        assert task.enabled is False

    def test_skill_schedule_md_precedence_admin_over_extra_over_bundled(
        self, config, tmp_path
    ):
        """SCHEDULE.md precedence is admin > extra > bundled.

        Uses `dream` (a real bundled skill) as the collision target.
        """
        # Admin copy of `dream` SCHEDULE.md with a distinct schedule.
        admin_dream = config.agent_path / "skills" / "dream"
        admin_dream.mkdir(parents=True)
        (admin_dream / "SKILL.md").write_text(
            "---\nname: dream\ndescription: Admin override\n---\nAdmin dream body.\n"
        )
        (admin_dream / "SCHEDULE.md").write_text(
            "---\nschedule: '15 4 * * *'\n---\nAdmin dream body.\n"
        )

        # Extra copy with yet another distinct schedule.
        extra_dream = tmp_path / "dream"
        extra_dream.mkdir()
        (extra_dream / "SKILL.md").write_text(
            "---\nname: dream\ndescription: Extra-paths override\n---\nExtra dream body.\n"
        )
        (extra_dream / "SCHEDULE.md").write_text(
            "---\nschedule: '30 5 * * *'\n---\nExtra dream body.\n"
        )
        config.extra_skill_paths = [str(extra_dream)]

        tasks = discover_schedules(config)
        dream_tasks = [t for t in tasks if t.name == "dream"]
        assert len(dream_tasks) == 1
        # Admin wins over both extra and bundled.
        assert dream_tasks[0].source == "admin"
        assert dream_tasks[0].schedule == "15 4 * * *"

        # Now remove the admin copy and re-check — extra should win over bundled.
        (admin_dream / "SCHEDULE.md").unlink()
        (admin_dream / "SKILL.md").unlink()
        admin_dream.rmdir()

        tasks = discover_schedules(config)
        dream_tasks = [t for t in tasks if t.name == "dream"]
        assert len(dream_tasks) == 1
        assert dream_tasks[0].source == "extra"
        # Extra SCHEDULE.md is forced disabled
        assert dream_tasks[0].enabled is False

    def test_discovers_extra_skill_path_schedule_md(self, config, tmp_path):
        """Skills in extra_skill_paths with SCHEDULE.md are discovered with source='extra'."""
        # Create a minimal scheduled skill in an isolated tmp dir.
        skill_dir = tmp_path / "ext-job"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: ext-job\ndescription: External scheduled job\n---\nDo external things.\n"
        )
        (skill_dir / "SCHEDULE.md").write_text(
            "---\nschedule: '0 7 * * *'\n---\nDo external things.\n"
        )
        config.extra_skill_paths = [str(skill_dir)]

        tasks = discover_schedules(config)
        names = {t.name for t in tasks}
        assert "ext-job" in names
        task = next(t for t in tasks if t.name == "ext-job")
        assert task.source == "extra"
        assert task.schedule == "0 7 * * *"
        # Extra path schedules are forced disabled
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
        from decafclaw.conversation_manager import ConversationManager
        manager = ConversationManager(config, EventBus())
        task = ScheduleTask(
            name="test-task", schedule="* * * * *",
            body="Do the thing.", source="admin", path=Path("/fake"),
            model="fast",
        )

        async def fake_run(ctx, user_message, history, **kwargs):
            from decafclaw.media import ToolResult
            return ToolResult(text="Done.")

        with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run):
            result = await run_schedule_task(config, EventBus(), manager, task)

        assert result["response"] == "Done."
        assert result["is_ok"] is False
        assert result["task_name"] == "test-task"
        # channel falls back to "schedule:{task.name}" when task.channel is empty
        assert result["channel"] == "schedule:test-task"

    @pytest.mark.asyncio
    async def test_heartbeat_ok_detected(self, config):
        from decafclaw.conversation_manager import ConversationManager
        manager = ConversationManager(config, EventBus())
        task = ScheduleTask(
            name="test-task", schedule="* * * * *",
            body="Check.", source="admin", path=Path("/fake"),
        )

        async def fake_run(ctx, user_message, history, **kwargs):
            from decafclaw.media import ToolResult
            return ToolResult(text="HEARTBEAT_OK — nothing to report.")

        with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run):
            result = await run_schedule_task(config, EventBus(), manager, task)

        assert result["is_ok"] is True

    @pytest.mark.asyncio
    async def test_handles_error(self, config):
        from decafclaw.conversation_manager import ConversationManager
        manager = ConversationManager(config, EventBus())
        task = ScheduleTask(
            name="failing-task", schedule="* * * * *",
            body="Fail.", source="admin", path=Path("/fake"),
        )

        with patch("decafclaw.agent.run_agent_turn",
                   new_callable=AsyncMock, side_effect=Exception("boom")):
            result = await run_schedule_task(config, EventBus(), manager, task)

        assert result["is_ok"] is False
        assert "boom" in result["response"]

    @pytest.mark.asyncio
    async def test_channel_in_result(self, config):
        from decafclaw.conversation_manager import ConversationManager
        manager = ConversationManager(config, EventBus())
        task = ScheduleTask(
            name="test", schedule="* * * * *",
            body="Report.", source="admin", path=Path("/fake"),
            channel="#reports",
        )

        async def fake_run(ctx, user_message, history, **kwargs):
            from decafclaw.media import ToolResult
            return ToolResult(text="Report done.")

        with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run):
            result = await run_schedule_task(config, EventBus(), manager, task)

        assert result["channel"] == "#reports"

    @pytest.mark.asyncio
    async def test_required_skill_body_injected_into_prompt(self, config):
        """Thin-trigger SCHEDULE.md must deliver the required-skill body to the LLM.

        Regression test for #558: pre-activated required-skills were being
        marked activated but their body was discarded, so the LLM saw only
        the one-line trigger.
        """
        from decafclaw.conversation_manager import ConversationManager
        from decafclaw.skills import SkillInfo

        config.discovered_skills = [
            SkillInfo(
                name="mastodon-ingest",
                description="Mastodon ingest",
                location=Path("/fake/skill/dir"),
                body="MASTODON-SKILL-BODY-MARKER\nFetch and summarize posts.",
            ),
        ]

        manager = ConversationManager(config, EventBus())
        task = ScheduleTask(
            name="mastodon-ingest", schedule="* * * * *",
            body="Time for the scheduled Mastodon ingestion. "
                 "Follow the mastodon-ingest skill instructions to completion.",
            source="extra", path=Path("/fake"),
            required_skills=["mastodon-ingest"],
        )

        captured: dict = {}

        async def fake_run(ctx, user_message, history, **kwargs):
            from decafclaw.media import ToolResult
            captured["user_message"] = user_message
            return ToolResult(text="done")

        with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run):
            await run_schedule_task(config, EventBus(), manager, task)

        prompt = captured["user_message"]
        assert "MASTODON-SKILL-BODY-MARKER" in prompt
        assert "<loaded_skills>" in prompt
        assert '<skill name="mastodon-ingest">' in prompt
        # Trigger text still present, body comes before trigger
        assert "Follow the mastodon-ingest skill instructions" in prompt
        assert prompt.index("MASTODON-SKILL-BODY-MARKER") < prompt.index(
            "Follow the mastodon-ingest skill instructions"
        )

    @pytest.mark.asyncio
    async def test_unknown_required_skill_skipped_gracefully(self, config):
        """Missing required-skill name logs a warning but doesn't raise."""
        from decafclaw.conversation_manager import ConversationManager

        config.discovered_skills = []  # nothing resolves
        manager = ConversationManager(config, EventBus())
        task = ScheduleTask(
            name="ghost-task", schedule="* * * * *",
            body="trigger", source="admin", path=Path("/fake"),
            required_skills=["does-not-exist"],
        )

        captured: dict = {}

        async def fake_run(ctx, user_message, history, **kwargs):
            from decafclaw.media import ToolResult
            captured["user_message"] = user_message
            return ToolResult(text="done")

        with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run):
            result = await run_schedule_task(config, EventBus(), manager, task)

        # No body to inject, but no crash and no <loaded_skills> wrapper
        assert "<loaded_skills>" not in captured["user_message"]
        assert result["response"] == "done"

    @pytest.mark.asyncio
    async def test_escape_hatch_tools_exempt_from_allow_list(self, config):
        """tool_search + activate_skill must pass the schedule allow-list filter.

        Regression test for #558: without this exemption the model has no way
        to recover from an under-spec'd task.
        """
        from decafclaw.conversation_manager import ConversationManager

        manager = ConversationManager(config, EventBus())
        task = ScheduleTask(
            name="locked-down", schedule="* * * * *",
            body="trigger", source="admin", path=Path("/fake"),
            allowed_tools=["vault_read", "current_time"],
        )

        captured: dict = {}

        async def fake_run(ctx, user_message, history, **kwargs):
            captured["allowed"] = set(ctx.tools.allowed or set())
            from decafclaw.media import ToolResult
            return ToolResult(text="done")

        with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run):
            await run_schedule_task(config, EventBus(), manager, task)

        assert "tool_search" in captured["allowed"]
        assert "activate_skill" in captured["allowed"]
        # Original allow-list entries preserved
        assert "vault_read" in captured["allowed"]
        assert "current_time" in captured["allowed"]

    @pytest.mark.asyncio
    async def test_routes_through_manager(self, config):
        """run_schedule_task routes turns through ConversationManager.enqueue_turn."""
        from decafclaw.conversation_manager import ConversationManager, TurnKind
        manager = ConversationManager(config, EventBus())
        task = ScheduleTask(
            name="routed-task", schedule="* * * * *",
            body="Do it.", source="admin", path=Path("/fake"),
        )

        seen = []
        orig_enqueue = manager.enqueue_turn

        async def spy_enqueue(conv_id, *, kind, prompt, **kwargs):
            seen.append({"conv_id": conv_id, "kind": kind})
            return await orig_enqueue(conv_id, kind=kind, prompt=prompt, **kwargs)

        manager.enqueue_turn = spy_enqueue

        async def fake_run(ctx, user_message, history, **kwargs):
            from decafclaw.media import ToolResult
            return ToolResult(text="done")

        with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run):
            result = await run_schedule_task(config, EventBus(), manager, task)

        assert len(seen) == 1
        assert seen[0]["kind"] is TurnKind.SCHEDULED_TASK
        assert seen[0]["conv_id"].startswith("schedule-routed-task-")
        assert result["task_name"] == "routed-task"


# -- Timer loop ----------------------------------------------------------------


class TestRunScheduleTimer:
    @pytest.mark.asyncio
    async def test_executes_due_task(self, config):
        from decafclaw.conversation_manager import ConversationManager
        manager = ConversationManager(config, EventBus())
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "test.md").write_text(
            "---\nschedule: '* * * * *'\n---\nDo the thing.\n"
        )

        shutdown = asyncio.Event()
        executed = []

        async def fake_run(cfg, bus, mgr, task):
            executed.append(task.name)
            return {"task_name": task.name, "channel": "", "response": "ok",
                    "is_ok": True, "context_id": None}

        with patch("decafclaw.schedules.run_schedule_task", side_effect=fake_run):
            async def stop_soon():
                await asyncio.sleep(0.05)
                shutdown.set()
            asyncio.create_task(stop_soon())
            await run_schedule_timer(config, EventBus(), manager, shutdown,
                                     poll_interval=0.02)

        assert "test" in executed

    @pytest.mark.asyncio
    async def test_skips_disabled_task(self, config):
        from decafclaw.conversation_manager import ConversationManager
        manager = ConversationManager(config, EventBus())
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "disabled.md").write_text(
            "---\nschedule: '* * * * *'\nenabled: false\n---\nSkip me.\n"
        )

        shutdown = asyncio.Event()
        executed = []

        async def fake_run(cfg, bus, mgr, task):
            executed.append(task.name)
            return {"task_name": task.name, "channel": "", "response": "ok",
                    "is_ok": True, "context_id": None}

        with patch("decafclaw.schedules.run_schedule_task", side_effect=fake_run):
            async def stop_soon():
                await asyncio.sleep(0.05)
                shutdown.set()
            asyncio.create_task(stop_soon())
            await run_schedule_timer(config, EventBus(), manager, shutdown,
                                     poll_interval=0.02)

        assert "disabled" not in executed

    @pytest.mark.asyncio
    async def test_no_tasks_no_crash(self, config):
        """Timer runs fine with no schedule files.

        Patches ``run_schedule_task`` so any bundled scheduled skills
        (``dream``, ``garden``) discovered from disk don't actually fire —
        on a fresh tmp_path config they'd be treated as "never run → due"
        and would try to run a real agent turn.
        """
        from decafclaw.conversation_manager import ConversationManager
        manager = ConversationManager(config, EventBus())
        shutdown = asyncio.Event()

        async def fake_run(cfg, bus, mgr, task):
            return {"task_name": task.name, "channel": "", "response": "ok",
                    "is_ok": True, "context_id": None}

        with patch("decafclaw.schedules.run_schedule_task", side_effect=fake_run):
            async def stop_soon():
                await asyncio.sleep(0.05)
                shutdown.set()
            asyncio.create_task(stop_soon())
            await run_schedule_timer(config, EventBus(), manager, shutdown,
                                     poll_interval=0.02)


# -- SCHEDULE.md sidecar discovery --------------------------------------------


class TestSkillScheduleFiles:
    """SCHEDULE.md sidecar discovery.

    These tests call ``discover_schedules`` directly — no timer loop runs and
    ``run_schedule_task`` is never invoked, so no patching is needed.
    """

    def test_bundled_skill_schedule_discovered(self, config):
        # Pre-condition: dream/garden/newsletter SCHEDULE.md exist in src/.
        tasks = {t.name: t for t in discover_schedules(config)}
        assert "dream" in tasks
        assert tasks["dream"].schedule == "0 3 * * *"
        assert tasks["dream"].source == "bundled"
        assert tasks["dream"].enabled is True

    def test_contrib_skill_schedule_forced_disabled(self, config, tmp_path):
        contrib_skill = tmp_path / "contrib_skills" / "news-monitor"
        contrib_skill.mkdir(parents=True)
        (contrib_skill / "SKILL.md").write_text(
            "---\nname: news-monitor\ndescription: Watch news.\n---\nDo it.\n"
        )
        (contrib_skill / "SCHEDULE.md").write_text(
            "---\nschedule: '0 * * * *'\nenabled: true\n---\nHourly check.\n"
        )
        config.extra_skill_paths = [str(tmp_path / "contrib_skills" / "news-monitor")]
        tasks = {t.name: t for t in discover_schedules(config)}
        assert "news-monitor" in tasks
        assert tasks["news-monitor"].enabled is False  # forced

    def test_admin_overlay_shadows_skill_schedule(self, config):
        overlay_dir = config.agent_path / "schedules"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        (overlay_dir / "dream.md").write_text(
            "---\nschedule: '0 4 * * *'\nenabled: false\n---\nUser-edited.\n"
        )
        tasks = {t.name: t for t in discover_schedules(config)}
        assert tasks["dream"].schedule == "0 4 * * *"
        assert tasks["dream"].enabled is False
        assert tasks["dream"].body == "User-edited."
        assert tasks["dream"].source == "admin"

    def test_workspace_standalone_shadows_skill_schedule(self, config):
        ws_dir = config.workspace_path / "schedules"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "dream.md").write_text(
            "---\nschedule: '0 5 * * *'\n---\nWorkspace version.\n"
        )
        tasks = {t.name: t for t in discover_schedules(config)}
        assert tasks["dream"].source == "workspace"
        assert tasks["dream"].schedule == "0 5 * * *"

    def test_admin_overlay_beats_workspace_standalone(self, config):
        admin_dir = config.agent_path / "schedules"
        admin_dir.mkdir(parents=True, exist_ok=True)
        ws_dir = config.workspace_path / "schedules"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (admin_dir / "dream.md").write_text(
            "---\nschedule: '0 4 * * *'\n---\nAdmin overlay.\n"
        )
        (ws_dir / "dream.md").write_text(
            "---\nschedule: '0 5 * * *'\n---\nWorkspace.\n"
        )
        tasks = {t.name: t for t in discover_schedules(config)}
        assert tasks["dream"].source == "admin"
        assert tasks["dream"].schedule == "0 4 * * *"

    def test_workspace_skill_schedule_md_skipped(self, config):
        ws_skill = config.workspace_path / "skills" / "sneaky"
        ws_skill.mkdir(parents=True)
        (ws_skill / "SKILL.md").write_text(
            "---\nname: sneaky\ndescription: x\n---\nDo it.\n"
        )
        (ws_skill / "SCHEDULE.md").write_text(
            "---\nschedule: '* * * * *'\n---\nShould not run.\n"
        )
        tasks = {t.name: t for t in discover_schedules(config)}
        assert "sneaky" not in tasks

    def test_skill_with_no_schedule_md_not_scheduled(self, config):
        tasks = {t.name: t for t in discover_schedules(config)}
        assert "vault" not in tasks
        assert "tabstack" not in tasks
