# Scheduled Tasks

DecafClaw supports cron-style scheduled tasks — markdown files with a cron expression that run as independent agent turns at specified times. This is separate from [Heartbeat](heartbeat.md), which runs all sections on a single interval.

Use scheduled tasks when you need time-of-day or day-of-week control, per-task intervals, or different configurations per task.

## Schedule file format

Each task is a markdown file with YAML frontmatter. The body is the prompt fed to the agent when the task fires.

```markdown
---
schedule: "0 9 * * 1-5"
channel: "abc123channelid"
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

### Frontmatter fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `schedule` | string | yes | — | 5-field cron expression (min hour dom month dow) |
| `channel` | string | no | — | Mattermost channel **ID** to report to. If omitted, output goes to agent log only. Channel name resolution (`#name`) is not yet supported. |
| `enabled` | bool | no | `true` | Quick toggle without deleting the file |
| `effort` | string | no | `default` | Effort level for model routing (`fast`, `default`, `strong`) |
| `allowed-tools` | list | no | all | Restrict which tools the task can use |
| `required-skills` | list | no | — | Skills to pre-activate before running the task |

### Cron expressions

Standard 5-field format: `minute hour day-of-month month day-of-week`

| Expression | Meaning |
|------------|---------|
| `0 9 * * 1-5` | Weekdays at 9:00 AM |
| `*/15 * * * *` | Every 15 minutes |
| `0 0 1 * *` | First of every month at midnight |
| `30 8,17 * * *` | 8:30 AM and 5:30 PM daily |
| `0 */6 * * *` | Every 6 hours |

Powered by [croniter](https://github.com/kiorky/croniter). Invalid expressions are logged as warnings and the task is skipped.

## File locations

Schedule files are discovered from two directories:

| Location | Writable by agent | Purpose |
|----------|-------------------|---------|
| `data/{agent_id}/schedules/*.md` | No | Admin-managed scheduled tasks |
| `data/{agent_id}/workspace/schedules/*.md` | Yes | Agent-managed tasks (created via `workspace_write`) |

- Task name is derived from the filename (minus `.md`)
- Admin tasks take precedence when names collide
- Both directories are scanned on every poll tick (changes take effect within 60 seconds)

## How it works

1. A timer loop polls every 60 seconds (independent of heartbeat)
2. On each tick, all schedule files are discovered and parsed
3. For each enabled task with a valid cron expression:
   - The per-task last-run timestamp is checked
   - `croniter` determines if the task was due since its last run
   - If due, the task executes as an independent agent turn
4. Last-run timestamps are stored per-task in `workspace/.schedule_last_run/`

### Overlap protection

If a task is still running from a previous tick, it's skipped. This prevents runaway concurrent executions of slow tasks.

### HEARTBEAT_OK

Same pattern as heartbeat — if a task has nothing to report, the agent should respond with `HEARTBEAT_OK` (case-insensitive, within the first 300 characters). OK responses are logged but not posted to channels.

## Reporting

- **With `channel`**: results are posted to the specified Mattermost channel ID
- **Without `channel`**: results are logged only (useful for tasks that write files or update workspace state)

## Task configuration

### Effort levels

Use `effort` to control which model runs the task:

```yaml
effort: fast      # cheap model for simple checks
effort: default   # normal model
effort: strong    # capable model for complex analysis
```

### Tool restrictions

Use `allowed-tools` to limit what the task can do:

```yaml
allowed-tools:
  - workspace_read
  - workspace_list
  - semantic_search
```

If omitted, all tools are available.

### Pre-activated skills

Use `required-skills` to activate skills before the task runs:

```yaml
required-skills:
  - tabstack
  - health
```

Skills are activated without permission checks (same as heartbeat admin sections).

## Examples

### Daily workspace summary

```markdown
---
schedule: "0 9 * * 1-5"
channel: "abc123channelid"
effort: default
---

Summarize what happened in the workspace in the last 24 hours.
Check for any new memories or conversation archives since yesterday.
Report key themes and anything that needs attention.
```

### Hourly health check (silent)

```markdown
---
schedule: "0 * * * *"
effort: fast
---

Run health_status and check for any issues.
If everything looks normal, respond with HEARTBEAT_OK.
If any MCP servers are down or heartbeat is overdue, write a note
to workspace memories.
```

### Weekly web check

```markdown
---
schedule: "0 10 * * 1"
channel: "abc123channelid"
effort: default
required-skills:
  - tabstack
---

Use Tabstack to check if https://example.com is up and responding.
Report the HTTP status code and page title.
```

### Disabled task (kept for reference)

```markdown
---
schedule: "0 6 * * *"
enabled: false
---

This task is disabled but the file is kept so it's easy to re-enable.
```

## Differences from heartbeat

| | Heartbeat | Scheduled Tasks |
|---|---|---|
| **Timing** | Single interval for all sections | Per-task cron expression |
| **Format** | `## Section` headers in one file | One file per task |
| **Config** | `HEARTBEAT_INTERVAL` env var | `schedule` frontmatter per file |
| **Effort** | Inherits default | Per-task `effort` field |
| **Tools** | All available | Per-task `allowed-tools` |
| **Skills** | None pre-activated | Per-task `required-skills` |
| **Timer** | Shared timer loop | Independent timer loop |

Both can coexist — heartbeat for simple recurring checks on a single interval, scheduled tasks for time-specific or individually-configured tasks.
