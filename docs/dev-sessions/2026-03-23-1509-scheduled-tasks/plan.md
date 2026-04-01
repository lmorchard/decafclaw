# Scheduled Tasks Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cron-style scheduled tasks as a new subsystem. Each task is a markdown file with YAML frontmatter (cron expression, channel, effort, tools, skills). A timer loop discovers tasks, checks if they're due via `croniter`, and executes them as independent agent turns.

**Architecture:** Schedule files live in `data/{agent_id}/schedules/` (admin) and `workspace/schedules/` (agent-writable). A `run_schedule_timer` polls every 60s, discovers files, checks cron expressions against per-task last-run timestamps, and runs due tasks via `run_agent_turn`. Results post to a configured Mattermost channel or log.

**Tech Stack:** Python, `croniter` library, existing context/agent/heartbeat patterns.

**Review notes (pre-execution):**
- Reuse `_split_frontmatter()` from `skills/__init__.py` instead of writing a new regex parser
- PyYAML already a dependency — no issue
- `MattermostClient` has no `resolve_channel` by name — heartbeat uses channel IDs directly. Need to add channel-name-to-ID resolution for `#channel-name` format in Task 6
- Heartbeat reporting uses its own httpx client (`_make_http_client`), not MattermostClient — schedule reporting should follow the same pattern for consistency

---

### Task 1: Add `croniter` dependency and schedule file parser

**Files:**
- Modify: `pyproject.toml` — add `croniter` dependency
- Create: `src/decafclaw/schedules.py` — schedule file parsing and discovery
- Create: `tests/test_schedules.py` — parser tests

- [ ] **Step 1: Add `croniter` dependency**

Add `croniter` to `pyproject.toml` dependencies, then run `uv sync`.

- [ ] **Step 2: Write failing tests for schedule file parsing**

```python
# tests/test_schedules.py
"""Tests for scheduled task parsing and discovery."""

import pytest

from decafclaw.schedules import ScheduleTask, parse_schedule_file


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
```

- [ ] **Step 3: Implement `ScheduleTask` dataclass and `parse_schedule_file`**

```python
# src/decafclaw/schedules.py
"""Scheduled tasks — cron-style task files with per-task scheduling."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from croniter import croniter

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
    required_skills: list[str] = field(default_factory=list)


# Regex to split YAML frontmatter from body
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


def parse_schedule_file(path: Path) -> ScheduleTask | None:
    """Parse a schedule markdown file. Returns None if invalid."""
    try:
        text = path.read_text()
    except OSError as e:
        log.warning(f"Cannot read schedule file {path}: {e}")
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        log.debug(f"No frontmatter in {path.name}, skipping")
        return None

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        log.warning(f"Invalid YAML in {path.name}: {e}")
        return None

    schedule = meta.get("schedule", "")
    if not schedule:
        log.warning(f"No schedule field in {path.name}, skipping")
        return None

    # Validate cron expression
    if not croniter.is_valid(schedule):
        log.warning(f"Invalid cron expression in {path.name}: {schedule!r}")
        return None

    body = match.group(2).strip()
    name = path.stem

    return ScheduleTask(
        name=name,
        schedule=schedule,
        body=body,
        source="",  # set by caller
        path=path,
        channel=meta.get("channel", ""),
        enabled=meta.get("enabled", True),
        effort=meta.get("effort", "default"),
        allowed_tools=meta.get("allowed-tools", []),
        required_skills=meta.get("required-skills", []),
    )
```

- [ ] **Step 4: Run tests**

Run: `make check && pytest tests/test_schedules.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```
feat: add croniter dependency and schedule file parser
```

---

### Task 2: Schedule file discovery

**Files:**
- Modify: `src/decafclaw/schedules.py` — add `discover_schedules()`
- Modify: `tests/test_schedules.py` — discovery tests

- [ ] **Step 1: Write failing tests for discovery**

```python
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
        assert len([t for t in tasks if t.name == "task"]) == 1
        task = [t for t in tasks if t.name == "task"][0]
        assert task.source == "admin"

    def test_empty_dirs(self, config):
        assert discover_schedules(config) == []
```

- [ ] **Step 2: Implement `discover_schedules()`**

```python
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

    return list(tasks_by_name.values())
```

- [ ] **Step 3: Run tests**

Run: `make check && pytest tests/test_schedules.py -v`
Expected: All pass.

- [ ] **Step 4: Commit**

```
feat: add schedule file discovery from admin and workspace dirs
```

---

### Task 3: Last-run tracking

**Files:**
- Modify: `src/decafclaw/schedules.py` — add last-run read/write and `is_due()`
- Modify: `tests/test_schedules.py` — last-run and due-check tests

- [ ] **Step 1: Write failing tests**

```python
class TestLastRun:
    def test_read_no_file(self, config):
        assert read_last_run(config, "nonexistent") == 0

    def test_write_and_read(self, config):
        write_last_run(config, "my-task", 1700000000.0)
        assert read_last_run(config, "my-task") == 1700000000.0


class TestIsDue:
    def test_never_run_is_due(self, config):
        """A task that has never run should be due."""
        task = ScheduleTask(
            name="test", schedule="* * * * *", body="test",
            source="admin", path=Path("/fake"),
        )
        assert is_due(config, task) is True

    def test_recently_run_not_due(self, config):
        """A task that just ran should not be due."""
        import time
        task = ScheduleTask(
            name="test", schedule="0 9 * * *", body="test",
            source="admin", path=Path("/fake"),
        )
        write_last_run(config, "test", time.time())
        assert is_due(config, task) is False
```

- [ ] **Step 2: Implement last-run tracking and `is_due()`**

```python
import time
from datetime import datetime


def _last_run_path(config, task_name: str) -> Path:
    return config.workspace_path / ".schedule_last_run" / task_name


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
    path.write_text(str(timestamp or time.time()))


def is_due(config, task: ScheduleTask) -> bool:
    """Check if a scheduled task is due to run."""
    last_run = read_last_run(config, task.name)
    if last_run == 0:
        return True  # never run before

    # Use croniter to find the next fire time after last_run
    cron = croniter(task.schedule, datetime.fromtimestamp(last_run))
    next_fire = cron.get_next(float)
    return time.time() >= next_fire
```

- [ ] **Step 3: Run tests**

Run: `make check && pytest tests/test_schedules.py -v`
Expected: All pass.

- [ ] **Step 4: Commit**

```
feat: add per-task last-run tracking and is_due() check
```

---

### Task 4: Task execution

**Files:**
- Modify: `src/decafclaw/schedules.py` — add `run_schedule_task()`
- Modify: `tests/test_schedules.py` — execution tests

- [ ] **Step 1: Write failing test for task execution**

```python
class TestRunScheduleTask:
    @pytest.mark.asyncio
    async def test_runs_agent_turn(self, config):
        """Task execution calls run_agent_turn with correct context."""
        from unittest.mock import AsyncMock, patch

        task = ScheduleTask(
            name="test-task", schedule="* * * * *",
            body="Do the thing.", source="admin", path=Path("/fake"),
            effort="fast",
        )
        mock_response = MagicMock()
        mock_response.text = "Done."

        with patch("decafclaw.schedules.run_agent_turn",
                    new_callable=AsyncMock, return_value=mock_response):
            result = await run_schedule_task(config, EventBus(), task)

        assert result["response"] == "Done."
        assert result["is_ok"] is False
        assert result["task_name"] == "test-task"
```

- [ ] **Step 2: Implement `run_schedule_task()`**

```python
async def run_schedule_task(config, event_bus, task: ScheduleTask) -> dict:
    """Run a single scheduled task as an agent turn.

    Returns {"task_name", "response", "is_ok", "context_id"}.
    """
    from .agent import run_agent_turn
    from .context import Context

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ctx = Context(config=config, event_bus=event_bus)
    ctx.user_id = f"schedule-{task.source}"
    ctx.channel_id = "schedule"
    ctx.channel_name = "schedule"
    ctx.conv_id = f"schedule-{task.name}-{timestamp}"
    ctx.effort = task.effort

    if task.allowed_tools:
        ctx.allowed_tools = set(task.allowed_tools)

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
                    log.error(f"Failed to activate skill '{skill_name}' for task '{task.name}': {e}")

    preamble = (
        f'You are running a scheduled task: "{task.name}".\n'
        "Execute the following task and report your findings.\n"
        "If there is nothing to report, respond with HEARTBEAT_OK.\n"
        "Prefer workspace tools (workspace_read, workspace_write, workspace_list) "
        "over shell commands.\n\n"
    )
    prompt = preamble + task.body

    try:
        result = await run_agent_turn(ctx, prompt, history=[])
        response = result.text or "(no response)"
        from .heartbeat import is_heartbeat_ok
        ok = is_heartbeat_ok(response)
        return {
            "task_name": task.name,
            "response": response,
            "is_ok": ok,
            "context_id": ctx.context_id,
        }
    except Exception as e:
        log.error(f"Scheduled task '{task.name}' failed: {e}", exc_info=True)
        return {
            "task_name": task.name,
            "response": f"[error: scheduled task failed: {e}]",
            "is_ok": False,
            "context_id": None,
        }
```

- [ ] **Step 3: Run tests**

Run: `make check && pytest tests/test_schedules.py -v`
Expected: All pass.

- [ ] **Step 4: Commit**

```
feat: add run_schedule_task() for executing scheduled tasks
```

---

### Task 5: Timer loop and runner integration

**Files:**
- Modify: `src/decafclaw/schedules.py` — add `run_schedule_timer()`
- Modify: `src/decafclaw/runner.py` — start schedule timer alongside heartbeat
- Modify: `tests/test_schedules.py` — timer tests

- [ ] **Step 1: Write failing test for timer loop**

```python
class TestRunScheduleTimer:
    @pytest.mark.asyncio
    async def test_executes_due_task(self, config):
        """Timer should execute tasks that are due."""
        # Create a schedule file with a "run every minute" cron
        admin = config.agent_path / "schedules"
        admin.mkdir(parents=True)
        (admin / "test.md").write_text(
            "---\nschedule: '* * * * *'\n---\nDo the thing.\n"
        )

        shutdown = asyncio.Event()
        executed = []

        async def fake_run(cfg, bus, task):
            executed.append(task.name)
            return {"task_name": task.name, "response": "ok",
                    "is_ok": True, "context_id": None}

        with patch("decafclaw.schedules.run_schedule_task", side_effect=fake_run):
            # Run one tick then shut down
            async def stop_after_tick():
                await asyncio.sleep(0.1)
                shutdown.set()
            asyncio.create_task(stop_after_tick())
            await run_schedule_timer(config, EventBus(), shutdown, poll_interval=0.05)

        assert "test" in executed
```

- [ ] **Step 2: Implement `run_schedule_timer()`**

```python
_SCHEDULE_POLL_INTERVAL = 60


async def run_schedule_timer(config, event_bus, shutdown_event,
                              on_result=None, poll_interval=None):
    """Run the schedule timer loop.

    Discovers schedule files, checks if tasks are due, and runs them.
    """
    interval = poll_interval or _SCHEDULE_POLL_INTERVAL
    running_tasks: set[str] = set()

    log.info("Schedule timer starting")

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass

        if shutdown_event.is_set():
            break

        tasks = discover_schedules(config)
        if not tasks:
            continue

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

            asyncio.create_task(_run())

    log.info("Schedule timer stopped")
```

- [ ] **Step 3: Wire into runner.py**

In `runner.py`, after the heartbeat task setup:

```python
# Start schedule timer
from .schedules import run_schedule_timer

schedule_on_result = None
if config.mattermost.url and config.mattermost.token:
    # Wire Mattermost reporting for scheduled tasks
    schedule_on_result = _make_schedule_reporter(config, client)

schedule_task = asyncio.create_task(
    run_schedule_timer(
        config, app_ctx.event_bus, shutdown_event,
        on_result=schedule_on_result,
    )
)
log.info("Schedule timer started")
```

Add `schedule_task` to the shutdown sequence (cancel before heartbeat).

- [ ] **Step 4: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 5: Commit**

```
feat: add schedule timer loop and wire into runner
```

---

### Task 6: Mattermost channel reporting

**Files:**
- Modify: `src/decafclaw/runner.py` — add schedule result reporter
- Modify: `tests/test_schedules.py` — reporting test

- [ ] **Step 1: Implement Mattermost reporting for scheduled tasks**

In `runner.py`, add a helper that posts schedule results to the configured channel:

```python
def _make_schedule_reporter(config, mattermost_client):
    """Create an on_result callback that posts to Mattermost."""
    async def _report(result):
        channel = result.get("channel", "")
        if not channel:
            return  # no channel configured, log only
        if result["is_ok"]:
            return  # suppress OK results

        response = result["response"]
        task_name = result["task_name"]
        text = f"**Scheduled task: {task_name}**\n\n{response}"

        # Resolve channel name to ID
        channel_id = await mattermost_client.resolve_channel(channel)
        if channel_id:
            await mattermost_client.send_message(channel_id, text)
        else:
            log.warning(f"Could not resolve channel '{channel}' for task '{task_name}'")

    return _report
```

Note: `run_schedule_task` result dict needs to include `channel` from the task. Update `run_schedule_task` to include `"channel": task.channel` in the returned dict.

- [ ] **Step 2: Add `resolve_channel` to MattermostClient if not present**

Check if the Mattermost client already has a method to resolve channel names to IDs. If not, add a simple one that calls the Mattermost API.

- [ ] **Step 3: Write test**

```python
class TestScheduleReporting:
    @pytest.mark.asyncio
    async def test_posts_to_channel(self):
        """Non-OK results with a channel get posted to Mattermost."""
        ...
```

- [ ] **Step 4: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 5: Commit**

```
feat: add Mattermost channel reporting for scheduled tasks
```

---

### Task 7: Health status and docs

**Files:**
- Modify: `src/decafclaw/tools/health.py` — add schedule status section
- Modify: `CLAUDE.md` — add key files, conventions
- Modify: `README.md` — add schedule config docs if applicable
- Create: `docs/schedules.md` — feature documentation

- [ ] **Step 1: Add schedule status to health tool**

Add a `_schedule_section()` and `get_schedule_data()` to `tools/health.py`:
- Number of discovered tasks (admin vs workspace)
- Next due task and when
- Last-run times

Wire into both `get_health_data()` (JSON) and `tool_health_status()` (markdown).

- [ ] **Step 2: Update CLAUDE.md**

Add to key files:
- `src/decafclaw/schedules.py` — Scheduled tasks: discovery, parsing, execution, timer

Add to conventions:
- "Scheduled tasks via cron-style files." Explain the two directories, frontmatter format, and how the timer works.

- [ ] **Step 3: Create `docs/schedules.md`**

Document the feature: file format, directories, frontmatter fields, examples, how the timer works.

- [ ] **Step 4: Run checks**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 5: Commit**

```
docs: add scheduled tasks to health status, CLAUDE.md, and docs
```
