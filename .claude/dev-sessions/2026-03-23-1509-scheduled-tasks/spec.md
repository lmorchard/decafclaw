# Scheduled Tasks (Cron-style)

## Overview

Extend DecafClaw with cron-style scheduled tasks. Each task is a markdown file with frontmatter specifying a cron schedule and execution options. The body is the prompt fed to the agent when the task fires.

This is a new subsystem alongside heartbeat — not an extension of it. Scheduled tasks have their own timer loop, per-task scheduling, and independent last-run tracking.

## References

- Issue: https://github.com/lmorchard/decafclaw/issues/8
- Related: #39 (heartbeat active hours / per-section intervals — partially overlaps)
- Deferred: Web UI named channel support (for viewing scheduled task output in web UI)

## Schedule File Format

Schedule files are markdown with YAML frontmatter. They live in two directories:

- `data/{agent_id}/schedules/` — admin-authored, read-only to the agent
- `data/{agent_id}/workspace/schedules/` — agent-writable, created under user direction

### Example

```markdown
---
schedule: "0 9 * * 1-5"
channel: "abc123channelid"
enabled: true
effort: default
allowed-tools:
  - workspace_read
  - workspace_list
  - semantic_search
required-skills:
  - tabstack
---

Summarize what happened in the workspace in the last 24 hours.
Check for any new memories or conversation archives since yesterday.
Report key themes and anything that needs attention.
```

### Frontmatter Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `schedule` | string | yes | — | 5-field cron expression (min hour dom month dow) |
| `channel` | string | no | — | Mattermost channel or user DM to report to. If omitted, output goes to agent log only |
| `enabled` | bool | no | `true` | Quick toggle without deleting the file |
| `effort` | string | no | `default` | Effort level for model routing (`fast`, `default`, `strong`) |
| `allowed-tools` | list | no | all | Restrict which tools the task can use |
| `required-skills` | list | no | — | Skills to pre-activate before running the task |

### File Discovery

- Scan both directories for `*.md` files
- Task name derived from filename (minus `.md` extension)
- Admin tasks take precedence if names collide (same as skill scan order)
- Files with `enabled: false` are discovered but skipped at execution time

## Cron Expression Parsing

Use the `croniter` library for cron expression evaluation.

- Standard 5-field format: `minute hour day-of-month month day-of-week`
- Examples: `0 9 * * 1-5` (weekdays at 9am), `*/15 * * * *` (every 15 min), `0 0 1 * *` (monthly)
- Invalid expressions logged as warnings, task skipped

## Last-Run Tracking

Each task tracks its last execution time independently.

- Storage: `workspace/.schedule_last_run/{task_name}` — plain text file containing epoch timestamp
- On startup, read existing timestamps to avoid re-firing tasks that already ran
- On each execution attempt, write the current time before running the task (prevents rapid re-runs on crash/restart)

## Timer Loop

A new `run_schedule_timer` function, independent of `run_heartbeat_timer`.

### Behavior

1. Poll every 60 seconds (same as heartbeat poll interval)
2. On each tick:
   - Discover all schedule files from both directories
   - For each enabled task with a valid cron expression:
     - Read last-run timestamp
     - Use `croniter` to check if the task was due since last run
     - If due, execute the task
3. Overlap protection: skip tasks that are still running from a previous tick
4. Graceful shutdown via the shared `shutdown_event`

### Runner Integration

- New task in `runner.py` alongside heartbeat, HTTP, and Mattermost
- Starts unconditionally (discovers tasks at runtime; no tasks = no-op)
- Does not depend on heartbeat being enabled

## Task Execution

Each scheduled task runs as an independent agent turn, similar to heartbeat sections.

### Execution Flow

1. Parse the schedule file (frontmatter + body)
2. Build prompt from the file body (with preamble similar to heartbeat sections)
3. Fork a `Context` with:
   - `user_id`: `"schedule-{source}"` (admin or workspace)
   - `conv_id`: `"schedule-{task_name}-{timestamp}"`
   - `effort`: from frontmatter (resolved via `resolve_effort`)
   - `allowed_tools`: from frontmatter if specified
4. Pre-activate `required-skills` (load tools onto context without permission checks)
5. Run `run_agent_turn` with the prompt and empty history
6. Handle response:
   - If `channel` specified: post to that Mattermost channel/user DM
   - If no channel: log the response
   - Apply `HEARTBEAT_OK` / suppress logic (reuse `is_heartbeat_ok`)

### Preamble

```
You are running a scheduled task: "{task_name}".
Execute the following task and report your findings.
If there is nothing to report, respond with HEARTBEAT_OK.
Prefer workspace tools (workspace_read, workspace_write, workspace_list) over shell commands.
```

## Configuration

No new config dataclass needed initially. The schedule timer discovers tasks from the filesystem.

Future enhancement: `ScheduleConfig` dataclass for global settings like:
- `poll_interval`: override the 60s default
- `suppress_ok`: global default (currently per-heartbeat)
- `default_channel`: fallback when tasks don't specify one

## What's NOT in Scope

- **Web UI channel support** — scheduled task output in web UI requires named channels, which don't exist yet. Deferred to a follow-up issue.
- **Agent self-scheduling tools** — tools like `create_schedule(name, cron, prompt)` could let the agent manage its own tasks via chat. Deferred — the agent can use `workspace_write` to create files in `workspace/schedules/` for now.
- **Cron extensions** — no support for seconds, `@reboot`, `@yearly` etc. Standard 5-field only.
- **Dependency chains** — no "run task B after task A completes" ordering.
- **Heartbeat migration** — existing HEARTBEAT.md sections continue to work as-is. No migration path needed.
