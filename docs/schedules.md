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
| `shell_patterns` | list | no (derived) | — | **Derived only** — extracted from `shell(...)` entries in `allowed-tools` on parse. Not settable as a literal frontmatter key (writing `shell_patterns:` lands in `unknown_keys` and is ignored). Appears as an independently-editable chip list in the UI under the Permissions group, and is round-tripped back to `allowed-tools` as `shell(...)` entries on save. Pre-approves matching commands, but only at admin or bundled tier — see [Permissions are tier-dependent](#permissions-are-tier-dependent). |
| `email-recipients` | list | no | — | Pre-approved email addresses for `send_email` that bypass confirmation for this task only, but only at admin or bundled tier — see [Permissions are tier-dependent](#permissions-are-tier-dependent). See [email.md](email.md#scheduled-task-integration). Exact addresses or `@domain.com` suffix patterns. |
| `pre_script` | string | no | — | Python script run **before** the turn; its stdout is injected into the prompt. Relative to `workspace/` or `data/{agent_id}/`. Not run at all outside admin or bundled tier — see [Permissions are tier-dependent](#permissions-are-tier-dependent). See [Pre-agent scripts](#pre-agent-scripts). |
| `required-skills` | list | no | — | Skills to pre-activate before running the task |

### Unrecognized keys

The parser recognizes the settable keys in the table above (all of them
except `shell_patterns`, which is derived rather than read), plus
`effort` as a legacy alias for `model`. Anything else in a schedule's
frontmatter is ignored at run time, logged at WARNING on load, and
listed in the web UI's raw section:

```
⚠ 2 keys are not recognized. Saving any field rewrites this file and
will remove them, along with any YAML comments: modle, efort
```

Ignored is not the same as preserved. Any write through
`PUT /api/schedules/{name}` — including a single-field edit from the UI
— re-serializes the whole file with `serialize_to_markdown`, which emits
only recognized keys. Unrecognized keys and YAML comments do not survive
that round trip.

This warning exists because a silently-dropped key is indistinguishable
from a key that took effect — the failure mode behind #729, where
`model: strong` was accepted and discarded for months.

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

## Discovery

### Standalone schedule files

Schedule files are discovered from two directories:

| Location | Writable by agent | Purpose |
|----------|-------------------|---------|
| `data/{agent_id}/schedules/*.md` | No | Admin-managed standalone tasks; also serves as the overlay store for skill SCHEDULE.md (see below) |
| `data/{agent_id}/workspace/schedules/*.md` | Yes | Agent-managed tasks (created via `workspace_write`) |

- Task name is derived from the filename (minus `.md`)
- Both directories are scanned on every poll tick (changes take effect within 60 seconds)

### Skill SCHEDULE.md sidecar

Any skill (bundled, admin-level, or contrib/`extra_skill_paths`) may ship a `SCHEDULE.md` file alongside its `SKILL.md`. The sidecar uses the same frontmatter format as a standalone schedule file (`schedule`, `enabled`, `model`, `allowed-tools`, `required-skills`, `email-recipients`) with a markdown body that is the prompt.

```markdown
---
schedule: "0 3 * * *"
required-skills:
  - vault
---

# Memory Consolidation

Review recent journal entries ...
```

The three bundled skills `dream`, `garden`, and `newsletter` each ship a `SCHEDULE.md` this way.

**Bundled and contrib SCHEDULE.md do not declare `model`.** Model config names are per-deployment — `vertex-gemini-pro` is a key in one agent's `config.json` and absent from another's — so a shipped skill has no portable name to reference. A task that wants a stronger model than `default_model` gets one through the admin overlay, where the names are known to exist. See [Model selection](#model-selection).

**Contrib default-disable:** Skills discovered from `extra_skill_paths` have their `enabled` flag forced to `false` regardless of what the SCHEDULE.md says. Installing a contrib skill should not silently activate a cron job. Users opt in via the admin overlay (see below).

**Workspace skills excluded:** Workspace skill directories (`workspace/skills/`) are not scanned for SCHEDULE.md — parallels the existing rule that workspace skills cannot self-schedule.

### Discovery precedence

When multiple sources define a task with the same name, the highest-precedence source wins entirely (no field-level merging):

1. `data/{agent_id}/schedules/{name}.md` — admin standalone; also acts as an overlay that shadows a same-named skill SCHEDULE.md
2. `workspace/schedules/{name}.md` — workspace standalone (agent-written)
3. Skill SCHEDULE.md, with internal precedence admin > extra > bundled

### Copy-on-write overlay

To customize a skill's SCHEDULE.md without editing the source file, write a full markdown file to `data/{agent_id}/schedules/{skill_name}.md`. This admin standalone file shadows the skill's SCHEDULE.md for as long as it exists. To revert to the skill's defaults, delete the overlay file.

The overlay is a full-file replacement — it is not merged with the original. All frontmatter fields must be present if they are needed (the overlay round-trips `allowed-tools`, `required-skills`, `model`, etc.).

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

When the cycle was genuinely quiet (nothing notable happened, no changes made), the agent prefixes its summary with `HEARTBEAT_OK` on a leading line before the quiet-cycle note. The scheduler's `is_heartbeat_ok()` check picks up the marker and logs a tidy `Schedule 'name': HEARTBEAT_OK` line instead of the response preview. The narrative still gets archived in full so the newsletter has material to quote.

The marker must **start** the response (leading whitespace is fine, case-insensitive, first 300 characters). As of #450 all response sentinels share one start-anchored matcher, `heartbeat.response_starts_with_sentinel` — a response that merely *mentions* `HEARTBEAT_OK` mid-text is not a quiet cycle. That matches what the preamble has always asked for, so no task wording needs to change.

A task whose turn ended abnormally is never treated as quiet: `is_heartbeat_ok()` returns `False` if the response carries `[Agent reached max tool iterations` or `[loop-breaker] Stopped`, regardless of a stray `HEARTBEAT_OK` in a mid-turn preamble. See [heartbeat docs](heartbeat.md#heartbeat_ok).

Historical note: older runs may end with a bare `HEARTBEAT_OK` token without narrative; the newsletter's `_is_status_token` filter handles those correctly for retrospective windows that reach into pre-change archives. Heartbeat also uses `HEARTBEAT_OK`; see [heartbeat docs](heartbeat.md).

### Pre-agent scripts

Mechanical work — fetch a feed, query a database, diff against the last run — does not need an LLM. A task can declare a script that runs *before* the turn:

```markdown
---
schedule: "0 9 * * *"
pre_script: scripts/fetch_feeds.py
---

Summarize anything new in the feed data above. If there are no new items,
reply with `[SILENT]` and nothing else.
```

The runner executes the script with the project interpreter and injects its stdout between the preamble and your task body:

```
<pre_script_output>
{"items": [...]}
</pre_script_output>
```

So the agent reads its instructions, then the data, then what to do with it — and spends no tool calls fetching.

**Mechanics**

- **Python only, no shell.** The script runs via the project interpreter directly (`sys.executable`), never through a shell, so there is no quoting or word-splitting surface. Schedules already have `allowed-tools: shell(...)` for shell work.
- **Path is sandboxed.** Resolved against `workspace/` then `data/{agent_id}/`, with containment checked after resolution — `../` escapes are rejected rather than followed.
- **stdout is the interface.** stderr is logged, never injected, so a script's warnings don't land in the model's context. Output is truncated at 8000 characters.
- **Environment.** The script gets `DECAFCLAW_AGENT_ID`, `DECAFCLAW_ROUTINE_NAME`, and `DECAFCLAW_WORKSPACE`, and runs with `workspace/` as its working directory. It has no access to decafclaw internals — it is an ordinary subprocess.
- **Failures are disclosed, not fatal.** A missing file, a non-zero exit, or a timeout injects an error line (`[pre_script error: exited 3]`) and the turn runs anyway, so the agent can report that the fetch failed. Aborting would produce silence, and silence is what `[SILENT]` exists to make a deliberate choice about.
- **Timeout** is `pre_script.timeout_sec` (default 60s), and is deliberately *not* charged against `agent.max_tool_iterations` — no reasoning has happened yet, so a slow fetch must not shrink the budget it is gathering data for. Disable the feature entirely with `pre_script.enabled = false`.

### Suppressing delivery with `[SILENT]`

A response that **starts** with `[SILENT]` emits no notification at all — and because every channel adapter (Mattermost DM, email, vault page) subscribes to that notification, suppressing it suppresses every channel. The turn is still archived in full.

Use it for "monitor X, alert only on change" routines, where the normal outcome is that there is nothing to say and a notification per run is just noise.

**`[SILENT]` does nothing unless your task body asks for it.** It is a mechanism, not a default: the shared scheduled-task preamble is deliberately left alone, because instructing *every* task to consider suppressing itself would be a policy change. Opt in per task:

```markdown
---
schedule: "*/15 * * * *"
---

Check the build status endpoint. If it is still green and unchanged since the
last run, reply with `[SILENT]` and nothing else. Otherwise describe what
changed.
```

Same rules as the other sentinels: start-anchored, leading whitespace allowed, case-insensitive, first 300 characters. A mid-response mention (`"not [SILENT] though"`) does not suppress. Failures are never suppressed — if the task raises, the alert notification is sent regardless of what the response said. A suppressed run logs one `Scheduled task 'name' returned [SILENT] — suppressing notification` line, so a quiet cycle stays distinguishable from a crashed one.

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

The value must be a key in `model_configs`. An unrecognized name falls back to `default_model` and logs:

```
WARNING Unknown model 'strong' requested — falling back to vertex-gemini-flash.
        Configured model_configs: vertex-gemini-flash, vertex-gemini-pro
```

Watch for this after upgrading. The old effort levels (`fast` / `default` / `strong`) are not model names and have not been since the effort system was removed — before the warning existed they were accepted and silently discarded (#729).

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

Skills are activated without permission checks (same as heartbeat admin sections). The full `SKILL.md` body of each listed skill is rendered as a `<loaded_skills><skill name="…">…</skill></loaded_skills>` block prepended to the task prompt, so a thin trigger body (e.g. *"Time for the scheduled Mastodon ingestion. Follow the mastodon-ingest skill instructions to completion."*) has the skill's instructions inline rather than relying on the LLM to ask for them. `$SKILL_DIR` is substituted to the skill's absolute location, matching `activate_skill`.

### Allow-list escape hatch

When a schedule supplies `allowed-tools`, the resulting allow-list is automatically extended with `tool_search` and `activate_skill`. Both are no-cost meta-tools that let the model recover if a task is under-spec'd (e.g. a required skill body fails to inject, or the allow-list misses a needed dependency). They do not grant capabilities by themselves.

### Permissions are tier-dependent

`allowed-tools`, its `shell(...)` entries, `email-recipients`, and
`pre_script` do two separate things:

- **Restrict** which tools the task can see. This applies at every tier.
- **Pre-approve** those tools, shell commands and recipients so they
  bypass confirmation. This applies **only at admin and bundled tier**.

`workspace/schedules/*.md` is agent-writable, so honouring its
pre-approvals would let the agent grant itself un-confirmed shell
execution — see #731. Contrib (`extra`) is excluded for the same reason;
opting a contrib schedule in means copying it to
`data/{agent_id}/schedules/`, which makes it admin tier and restores
pre-approval as a deliberate human act.

Scheduled turns are unattended, so a shell command that matches no
pre-approval and no entry in `shell_allow_patterns.json` is **denied**,
not prompted. At workspace tier, `shell(...)` in frontmatter therefore
narrows what the task may attempt without granting anything.

`pre_script` is not run at all at an untrusted tier. Unlike the others it
has no approval path to fall through to — it executes arbitrary Python as
the bot process — so the script is skipped and the prompt receives
`[pre_script error: ignored — not permitted at this tier]` in place of its
output. The task still runs; it just doesn't get the script's data.

**Migration:** a workspace-tier schedule that relied on `shell(...)`
pre-approval stops working. Move it to `data/{agent_id}/schedules/` to
restore it — that move is the deliberate act the boundary requires.

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

## HTTP API

Four REST endpoints allow listing and editing schedules without touching files directly. All endpoints require authentication (same session cookie as the web UI).

### `GET /api/schedules`

Returns all discovered schedules with metadata.

**Response:**

```json
{
  "schedules": [
    {
      "name": "dream",
      "source_tier": "bundled",
      "source_path": "/path/to/skills/dream/SCHEDULE.md",
      "has_overlay": false,
      "enabled": true,
      "schedule": "0 3 * * *",
      "channel": "",
      "model": "",
      "allowed_tools": [],
      "required_skills": ["vault"],
      "body": "# Memory Consolidation\n...",
      "modified": 1747598400.0,
      "next_run_iso": "2026-05-20T03:00:00+00:00",
      "last_run_iso": "2026-05-19T03:00:00+00:00"
    }
  ]
}
```

**`source_tier`** values: `bundled`, `admin`, `extra`, `workspace`.

**`has_overlay`** is `true` when an admin standalone file at `data/{agent_id}/schedules/{name}.md` is shadowing a same-named skill SCHEDULE.md. `false` for workspace-tier or pure standalone admin entries.

**`modified`** is the Unix mtime (float seconds) of the source file. Used by the body editor for conflict detection.

**`last_run_iso`** is `null` if the task has never run.

### `GET /api/schedules/{name}`

Returns a single schedule entry by name. Same shape as a list entry wrapped in `{"schedule": ...}`, plus top-level `body` and `modified` mirroring `schedule.body` / `schedule.modified` — the same wiki-editor contract the `PUT` response satisfies (see [web-ui.md](web-ui.md#the-wiki-editor-host-contract)). The editor's conflict-banner Reload fetches this endpoint and has no knowledge of the `schedule` envelope.

**Error codes:**
- `404` — name not found

### `PUT /api/schedules/{name}`

Apply a partial update to a schedule. Writes the full effective state (current resolved values merged with the patch).

- If the source was a skill SCHEDULE.md, this creates an admin overlay at `data/{agent_id}/schedules/{name}.md`.
- If the source was already an admin standalone file, this updates it in place.
- If the source is a workspace-tier file (`workspace/schedules/{name}.md`), this edits it in place — workspace schedules are user-editable.

**Request body** (all fields optional):

```json
{
  "enabled": false,
  "schedule": "0 4 * * *",
  "body": "New prompt body.",
  "channel": "abc123channelid",
  "allowed_tools": ["workspace_read"],
  "required_skills": ["vault"],
  "model": "gemini-flash"
}
```

The `content` key is accepted as an alias for `body` — this is the shape wiki-editor sends (`{content, modified}`). The `modified` key is accepted and silently discarded (wiki-editor sends it for conflict detection; the server does not enforce it yet).

Fields not included in the request body are preserved from the current effective state.

**Response:** `{"schedule": <schedule object>, "modified": <float>}` — the updated entry with the new file mtime. The top-level `modified` field mirrors `schedule.modified` for wiki-editor's conflict-detection contract.

**Error codes:**
- `400` — unsafe or invalid name, or invalid cron expression
- `404` — name not found

### `POST /api/schedules/{name}/run`

Fire a schedule immediately, regardless of its `enabled` flag or cron expression. Manual invocation is treated as explicit intent. Writes the `last_run` timestamp so the cron timer does not double-fire shortly after.

The task runs in the background; the response returns immediately with the conv_id of the new conversation. Find the run in the system conversations list.

**Response (202 Accepted):**

```json
{
  "conv_id": "schedule-dream-20260519-114230",
  "task_name": "dream",
  "started_at": "2026-05-19T11:42:30.123456+00:00"
}
```

**Error codes:**
- `404` — name not found

### `DELETE /api/schedules/{name}/overlay`

Revert a schedule to its skill SCHEDULE.md default by deleting the admin overlay file.

Only meaningful when `has_overlay: true`. If no overlay exists, returns **404**.

**Response:** `{"schedule": <schedule object>}` — the post-revert entry reflecting the original skill SCHEDULE.md values.

**Error codes:**
- `404` — no overlay exists for this name, or name not found after overlay removal

### `GET /api/models`

Returns all available model configs for populating UI dropdowns.

**Response:**

```json
{
  "models": ["gemini-flash", "gemini-pro", "vertex-gemini-flash"],
  "default": "gemini-flash"
}
```

**Fields:**
- **`models`** — sorted list of model config names available in `config.model_configs`
- **`default`** — the `config.default_model` used when a schedule omits the `model` field

## Editing from the web UI

The schedules page exposes every frontmatter field. Edits PUT to
`/api/schedules/{name}`, which writes an admin overlay for skill-supplied
schedules and edits standalone files in place.

`allowed-tools`, shell patterns, `email-recipients` and `pre_script` are
grouped and marked separately: at admin or bundled tier they pre-approve
actions that would otherwise require confirmation. At workspace or extra
tier they still restrict, but grant no pre-approval — see
[Permissions are tier-dependent](#permissions-are-tier-dependent). `pre_script`
is in that group because it runs arbitrary Python as the bot process on
the next fire — the most powerful setting on the panel — and unlike the
others it doesn't run at all outside admin/bundled tier.

The model field is a dropdown populated from
[`GET /api/models`](#get-apimodels). If that request fails the field
falls back to a plain text input so it stays editable; a *successful*
response with an empty list (an agent with no `model_configs`) is a real
state and still shows the dropdown.

The raw section is read-only. Schedule frontmatter maps onto a fixed set
of fields, so once each has a control there is nothing an editable raw
box could reach — and a key typed there would be dropped on the next
write.

## Sidebar UI

The **Schedules** tab in the conversation sidebar (`schedules-sidebar.js`) provides a point-and-click interface for the REST API described above. Open it by clicking "Schedules" in the sidebar tab strip.

### List view

Each row displays:
- **Name** — the schedule's task name (directory basename for skill SCHEDULE.md sidecars, filename stem for standalone files). Click to open the side-panel editor.
- **Source tier badge** — one of `bundled`, `admin`, `extra`, or `workspace`. Color-coded: bundled = green, admin = primary accent, extra/workspace = muted.
- **"overridden" pill** — shown when an admin overlay is shadowing a skill SCHEDULE.md; disappears after a reset.
- **Enabled toggle** — a checkbox that calls `PUT /api/schedules/{name}` with `{enabled: !current}`.
- **Cron expression** and **estimated next run** (`in Xm / in Xh / in Xd / overdue`) below the header row.

### Side-panel editor

Clicking a row opens the schedule in the `#wiki-main` side panel — the same surface used by vault pages, workspace files, and agent config. The panel shows:

- **Header**: back arrow (closes the panel), name, source tier badge, "overridden" pill, a **"Run now"** button (fires the task immediately, bypassing cron and the enabled flag), and a "Reset to default" button when an overlay is shadowing a skill SCHEDULE.md.
- **Metadata panel** (`<schedule-metadata>`): every frontmatter field — cron expression, channel, model, enabled checkbox, and chip lists for required skills, allowed tools, shell patterns, and email recipients. Allowed tools, shell patterns, email recipients and the pre-script path are grouped under a **"Permissions — these bypass confirmation"** header; at workspace or extra tier a note under that header names the tier condition (see [Permissions are tier-dependent](#permissions-are-tier-dependent)). Each field saves on `change`; no separate Save button. The panel scrolls independently of the body editor when the viewport is short.
- **Raw section**: a read-only view of the schedule's frontmatter as it sits on disk, with a warning naming any unrecognized keys and noting that the next save removes them. Read-only because the field set is closed — anything typed there that is not a known field would be dropped on the next write.
- **Body editor**: a `<wiki-editor>` (Milkdown) for the prompt body. Autosaves after 1 second of inactivity or on Ctrl+S / focus-out.
- **URL deep-linking**: opening a schedule sets `?schedule={name}` in the URL; the panel restores on page reload or direct link.

### Workspace-tier entries

Schedules sourced from `workspace/schedules/` (agent-written) are fully editable. Saves write in-place to `workspace/schedules/{name}.md`. No "Reset to default" button (workspace files have no skill SCHEDULE.md to fall back to).

### Refresh behavior

The list fetches fresh data from `/api/schedules` each time the tab becomes active. After any save or reset within the session, a `schedule-saved` window event triggers an immediate re-fetch so the list stays current without a manual page reload.

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
