# Scheduled Tasks

DecafClaw supports cron-style scheduled tasks — markdown files with a cron expression that run as independent agent turns at specified times. This is separate from [Heartbeat](heartbeat.md), which runs all sections on a single interval.

Use scheduled tasks when you need time-of-day or day-of-week control, per-task intervals, or different configurations per task.

## Schedule file format

Each task is a markdown file with YAML frontmatter. The body is the prompt fed to the agent when the task fires.

```markdown
---
schedule: "0 9 * * 1-5"
channel: "abc123channelid"
model: gemini-flash
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
| `model` | string | no | — | Named model config for this task. Omit to use `default_model`. |
| `allowed-tools` | list | no | all | Restrict which tools the task can use |
| `required-skills` | list | no | — | Skills to pre-activate before running the task |
| `email-recipients` | list | no | — | Pre-approved email addresses for `send_email` that bypass confirmation for this task only. See [email.md](email.md#scheduled-task-integration). Exact addresses or `@domain.com` suffix patterns. |

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

### Skill schedule frontmatter

Skills can also declare schedules in their SKILL.md frontmatter:

```yaml
---
name: dream
schedule: "0 * * * *"
model: gemini-pro
required-skills:
  - wiki
user-invocable: true
context: fork
---
```

This makes a skill both a user command (`!dream`) and a scheduled task — no separate schedule file needed.

**Trust boundary:** only bundled skills (`src/decafclaw/skills/`) and admin-level skills (`data/{agent_id}/skills/`) can declare schedules. Workspace skills are ignored.

File-based schedules take precedence over skill schedules when names collide.

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

### Final summary

The scheduled-task preamble instructs the agent to end its turn with a short narrative summary of what happened this cycle — always, even when the cycle was quiet. This keeps archived scheduled-task conversations readable and gives retrospective tools (e.g., the `!newsletter` composer) real material to quote.

When the cycle was genuinely quiet (nothing notable happened, no changes made), the agent prefixes its summary with `HEARTBEAT_OK` on a leading line before the quiet-cycle note. The scheduler's `is_heartbeat_ok()` check (case-insensitive, first 300 chars) picks up the marker and logs a tidy `Schedule 'name': HEARTBEAT_OK` line instead of the response preview. The narrative still gets archived in full so the newsletter has material to quote.

Historical note: older runs may end with a bare `HEARTBEAT_OK` token without narrative; the newsletter's `_is_status_token` filter handles those correctly for retrospective windows that reach into pre-change archives. Heartbeat also uses `HEARTBEAT_OK`; see [heartbeat docs](heartbeat.md).

## Reporting

- **With `channel`**: results are posted to the specified Mattermost channel ID
- **Without `channel`**: results are logged only (useful for tasks that write files or update workspace state)

## Task configuration

### Model selection

Use `model` to control which model runs the task:

```yaml
model: gemini-flash   # use a specific named model config
```

Omit to use the `default_model` from config. See [Model Selection](model-selection.md).

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
model: gemini-flash
---

Summarize what happened in the workspace in the last 24 hours.
Check for any new memories or conversation archives since yesterday.
Report key themes and anything that needs attention.
```

### Hourly health check (silent)

```markdown
---
schedule: "0 * * * *"
model: gemini-flash
---

Run health_status and check for any issues.
If any MCP servers are down or heartbeat is overdue, write a note
to workspace memories and describe what you saw. If everything
looks normal, begin your summary with HEARTBEAT_OK on its own line
and say so briefly — the marker keeps the log line short.
```

### Weekly web check

```markdown
---
schedule: "0 10 * * 1"
channel: "abc123channelid"
model: gemini-flash
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
| **Model** | Inherits `default_model` | Per-task `model` field |
| **Tools** | All available | Per-task `allowed-tools` |
| **Skills** | None pre-activated | Per-task `required-skills` |
| **Timer** | Shared timer loop | Independent timer loop |

Both can coexist — heartbeat for simple recurring checks on a single interval, scheduled tasks for time-specific or individually-configured tasks.
