"""Scheduled tasks — cron-style task files with per-task scheduling."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter

from .skills import _parse_allowed_tools, _split_frontmatter

log = logging.getLogger(__name__)


@dataclass
class ScheduleTask:
    """A parsed schedule file."""
    name: str
    schedule: str  # 5-field cron expression
    body: str
    source: str  # "admin" or "workspace"
    path: Path
    channel: str = ""
    enabled: bool = True
    effort: str = "default"
    allowed_tools: list[str] = field(default_factory=list)
    shell_patterns: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)


def parse_schedule_file(path: Path) -> ScheduleTask | None:
    """Parse a schedule markdown file. Returns None if invalid."""
    try:
        text = path.read_text()
    except OSError as e:
        log.warning(f"Cannot read schedule file {path}: {e}")
        return None

    meta, body = _split_frontmatter(text)
    if meta is None:
        log.debug(f"No frontmatter in {path.name}, skipping")
        return None

    schedule = meta.get("schedule", "")
    if not schedule:
        log.warning(f"No schedule field in {path.name}, skipping")
        return None

    if not croniter.is_valid(schedule):
        log.warning(f"Invalid cron expression in {path.name}: {schedule!r}")
        return None

    # Type coercion — YAML can produce unexpected types (e.g. 'false' as string)
    enabled = meta.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = str(enabled).lower() not in ("false", "0", "no", "off")

    allowed_tools_raw = meta.get("allowed-tools", "")
    if allowed_tools_raw is None:
        allowed_tools_raw = ""
    elif isinstance(allowed_tools_raw, list):
        allowed_tools_raw = ", ".join(str(t) for t in allowed_tools_raw)
    allowed_tools, shell_patterns = _parse_allowed_tools(str(allowed_tools_raw))

    required_skills = meta.get("required-skills", [])
    if not isinstance(required_skills, list):
        required_skills = [str(required_skills)] if required_skills else []

    return ScheduleTask(
        name=path.stem,
        schedule=schedule,
        body=body.strip(),
        source="",  # set by caller
        path=path,
        channel=str(meta.get("channel", "")),
        enabled=enabled,
        effort=str(meta.get("effort", "default")),
        allowed_tools=allowed_tools,
        shell_patterns=shell_patterns,
        required_skills=[str(s) for s in required_skills],
    )


# -- Discovery ----------------------------------------------------------------


def discover_schedules(config) -> list[ScheduleTask]:
    """Discover schedule files from admin and workspace directories.

    Admin tasks take precedence when names collide.
    """
    tasks_by_name: dict[str, ScheduleTask] = {}

    for source, base_dir in [
        ("admin", config.agent_path / "schedules"),
        ("workspace", config.workspace_path / "schedules"),
    ]:
        if not base_dir.is_dir():
            continue
        for path in sorted(base_dir.glob("*.md")):
            task = parse_schedule_file(path)
            if task is None:
                continue
            task.source = source
            # Admin takes precedence
            if task.name not in tasks_by_name or source == "admin":
                tasks_by_name[task.name] = task

    # Also discover scheduled skills from disk (bundled + admin only, not workspace).
    # Re-reads SKILL.md files each poll so edits (e.g. enabled: false) take
    # effect without restart.
    from .skills import _BUNDLED_SKILLS_DIR, parse_skill_md
    bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
    admin_skills_dir = (config.agent_path / "skills").resolve()

    for source_label, base_dir in [
        ("bundled", bundled_dir),
        ("admin", admin_skills_dir),
    ]:
        if not base_dir.is_dir():
            continue
        for skill_dir in sorted(base_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            skill = parse_skill_md(skill_md)
            if skill is None or not skill.schedule:
                continue
            if not croniter.is_valid(skill.schedule):
                log.warning(f"Invalid cron in skill '{skill.name}': {skill.schedule!r}")
                continue
            # File-based schedules take precedence
            if skill.name in tasks_by_name:
                continue
            tasks_by_name[skill.name] = ScheduleTask(
                name=skill.name,
                schedule=skill.schedule,
                body=skill.body,
                source=source_label,
                path=skill_md,
                enabled=skill.enabled,
                effort=skill.effort or "default",
                allowed_tools=skill.allowed_tools,
                shell_patterns=skill.shell_patterns,
                required_skills=skill.requires_skills,
            )

    return list(tasks_by_name.values())


# -- Last-run tracking --------------------------------------------------------


def _safe_task_name(task_name: str) -> str:
    """Sanitize task name for use in filesystem paths."""
    # Strip path separators and dots, keep alphanumeric + hyphens + underscores
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", task_name)
    if not safe or safe in (".", ".."):
        safe = "_invalid_"
    return safe


def _last_run_path(config, task_name: str) -> Path:
    base = config.workspace_path / ".schedule_last_run"
    path = (base / _safe_task_name(task_name)).resolve()
    # Verify path stays under the base directory
    if not str(path).startswith(str(base.resolve())):
        raise ValueError(f"Task name resolves outside last-run directory: {task_name!r}")
    return path


def read_last_run(config, task_name: str) -> float:
    """Read last-run timestamp. Returns 0 if never run."""
    path = _last_run_path(config, task_name)
    try:
        if path.exists():
            return float(path.read_text().strip())
    except (ValueError, OSError):
        pass
    return 0


def write_last_run(config, task_name: str, timestamp: float | None = None) -> None:
    """Write last-run timestamp (defaults to now)."""
    path = _last_run_path(config, task_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time() if timestamp is None else timestamp))


def is_due(config, task: ScheduleTask) -> bool:
    """Check if a scheduled task is due to run."""
    last_run = read_last_run(config, task.name)
    if last_run == 0:
        return True  # never run before

    cron = croniter(task.schedule, datetime.fromtimestamp(last_run, tz=timezone.utc))
    next_fire = cron.get_next(float)
    return time.time() >= next_fire


# -- Task execution -----------------------------------------------------------


async def run_schedule_task(config, event_bus, task: ScheduleTask) -> dict:
    """Run a single scheduled task as an agent turn.

    Returns {"task_name", "channel", "response", "is_ok", "context_id"}.
    """
    from .agent import run_agent_turn
    from .context import Context

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    channel = task.channel or f"schedule:{task.name}"

    allowed_tools_set = None
    preapproved = set()
    if task.allowed_tools or task.shell_patterns:
        allowed_tools_set = set(task.allowed_tools)
        if task.shell_patterns:
            allowed_tools_set.add("shell")  # ensure shell tool is visible
        preapproved = set(task.allowed_tools)

    skill_dir = str(task.path.parent.resolve())
    shell_patterns = None
    if task.shell_patterns:
        shell_patterns = [
            p.replace("$SKILL_DIR", skill_dir) for p in task.shell_patterns
        ]

    ctx = Context.for_task(
        config, event_bus,
        user_id=f"schedule-{task.source}",
        conv_id=f"schedule-{task.name}-{timestamp}",
        channel_id=channel,
        channel_name=channel,
        effort=task.effort,
        task_mode="scheduled",
        allowed_tools=allowed_tools_set,
        preapproved_tools=preapproved,
        preapproved_shell_patterns=shell_patterns,
    )

    # Pre-activate required skills
    if task.required_skills:
        discovered = getattr(config, "discovered_skills", [])
        skill_map = {s.name: s for s in discovered}
        from .tools.skill_tools import activate_skill_internal
        for skill_name in task.required_skills:
            skill_info = skill_map.get(skill_name)
            if skill_info:
                try:
                    await activate_skill_internal(ctx, skill_info)
                except Exception as e:
                    log.error(f"Failed to activate skill '{skill_name}' "
                              f"for task '{task.name}': {e}")

    from .polling import build_task_preamble

    preamble = build_task_preamble("scheduled task", task.name)
    from .commands import substitute_body
    body = substitute_body(task.body, skill_dir=str(task.path.parent.resolve()))
    prompt = preamble + body

    try:
        result = await run_agent_turn(ctx, prompt, history=[])
        response = result.text or "(no response)"
        from .heartbeat import is_heartbeat_ok
        ok = is_heartbeat_ok(response)
        return {
            "task_name": task.name,
            "channel": task.channel,
            "response": response,
            "is_ok": ok,
            "context_id": ctx.context_id,
        }
    except Exception as e:
        log.error(f"Scheduled task '{task.name}' failed: {e}", exc_info=True)
        return {
            "task_name": task.name,
            "channel": task.channel,
            "response": f"[error: scheduled task failed: {e}]",
            "is_ok": False,
            "context_id": None,
        }


# -- Timer loop ---------------------------------------------------------------


_SCHEDULE_POLL_INTERVAL = 60


async def run_schedule_timer(config, event_bus, shutdown_event,
                              on_result=None, poll_interval=None):
    """Run the schedule timer loop.

    Discovers schedule files, checks if tasks are due, and runs them.
    """
    from .polling import run_polling_loop

    interval = poll_interval or _SCHEDULE_POLL_INTERVAL
    running_tasks: set[str] = set()
    in_flight: set[asyncio.Task] = set()

    log.info("Schedule timer starting")

    async def _tick():
        tasks = discover_schedules(config)
        if not tasks:
            return

        for task in tasks:
            if not task.enabled:
                continue
            if task.name in running_tasks:
                log.debug(f"Schedule '{task.name}' still running, skipping")
                continue
            if not is_due(config, task):
                continue

            log.info(f"Schedule '{task.name}' is due, executing")
            running_tasks.add(task.name)

            async def _run(t=task):
                try:
                    write_last_run(config, t.name)
                    result = await run_schedule_task(config, event_bus, t)
                    if on_result:
                        try:
                            await on_result(result)
                        except Exception as e:
                            log.error(f"Schedule result callback failed: {e}")
                    elif result["is_ok"]:
                        log.info(f"Schedule '{t.name}': HEARTBEAT_OK")
                    else:
                        log.info(f"Schedule '{t.name}' response: "
                                 f"{result['response'][:200]}")
                except Exception as e:
                    log.error(f"Schedule '{t.name}' execution failed: {e}")
                finally:
                    running_tasks.discard(t.name)

            t = asyncio.create_task(_run())
            in_flight.add(t)
            t.add_done_callback(in_flight.discard)

    try:
        await run_polling_loop(
            interval=interval,
            shutdown_event=shutdown_event,
            on_tick=_tick,
            label="Schedule",
        )
    finally:
        # Await in-flight tasks on shutdown
        if in_flight:
            log.info(f"Waiting for {len(in_flight)} in-flight scheduled task(s)")
            await asyncio.gather(*in_flight, return_exceptions=True)

    log.info("Schedule timer stopped")
