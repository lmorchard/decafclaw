# Heartbeat Prompt — Spec

## Goal

Periodic agent wake-up that reads HEARTBEAT.md files and performs the tasks described within. Enables proactive monitoring, recurring checks, and one-off tasks without user interaction.

## Design Principles

- **Compatible with OpenClaw heartbeat**: plain markdown HEARTBEAT.md, `HEARTBEAT_OK` suppression pattern
- **Improved execution**: split on `##` headers, run each section as a separate agent turn for reliable execution
- **Admin + agent authorship**: admin-level HEARTBEAT.md merged with workspace-level at runtime
- **Threaded reporting**: top-level post per cycle, threaded replies per section
- **No overlap**: skip heartbeat tick if previous cycle is still running (with logging)

## HEARTBEAT.md Format

Plain markdown. Sections delimited by `##` headers. Each section is a standalone directive run as its own agent turn.

```markdown
## Check deploy status

Look up the current deploy pipeline status. Report if anything is stuck or failing.

## RSS feed check

Check the following RSS feeds for new posts since last check:
- https://example.com/feed.xml
Use workspace to store the last-seen timestamps.

## One-off: verify DNS migration

Check that example.com resolves to 1.2.3.4. If it does, report success and remove this section from HEARTBEAT.md.
```

No frontmatter or special structure — just markdown with `##` sections.

Content before the first `##` header is treated as its own section. This means a simple checklist with no headers works as a single section (OpenClaw compatibility).

## File Locations

Two HEARTBEAT.md files, merged at runtime:

| Priority | Location | Writable by agent | Purpose |
|----------|----------|-------------------|---------|
| 1 (base) | `data/{agent_id}/HEARTBEAT.md` | No | Admin-managed recurring tasks |
| 2 (extra) | `data/{agent_id}/workspace/HEARTBEAT.md` | Yes | Agent-managed tasks (one-offs, self-scheduled) |

Both files are read and their sections concatenated. Admin sections run first. If either file is missing, it's skipped (not an error). If both are missing, the heartbeat tick is a no-op.

## Execution Flow

### Per Tick

1. Timer fires (every `HEARTBEAT_INTERVAL`, default 30m)
2. Check if previous cycle is still running → if so, log warning and skip
3. Read and merge HEARTBEAT.md files
4. Split merged content into sections on `##` headers
5. If no sections found, skip (no-op)
6. **Mattermost**: post a top-level heartbeat marker message to the reporting channel
7. For each section (sequentially):
   - Create an isolated agent turn (fresh history, no carryover between sections)
   - Feed the section content as the user message
   - Full tool set available
   - Collect the response
8. **Report results** (see Reporting below)
9. Mark cycle complete

### Section Agent Turns

Each section runs as an independent `run_agent_turn`:
- Fresh empty history (isolated — no shared state between sections)
- New context (not forked — clean slate) with a heartbeat-specific `conv_id` (e.g., `heartbeat-{timestamp}-{section_index}`)
- Context fields: `user_id` = "heartbeat", `channel_id` = reporting channel or "heartbeat", `conv_id` = unique per section
- Full tool access (same as user conversations)
- Section header included in the prompt for context

### Progress Events

Each heartbeat section has its own `context_id`. In Mattermost mode, a progress subscriber is registered per section that posts tool status updates as edits to that section's threaded reply. Same pattern as user conversation progress handling.

The prompt sent to the agent for each section:

```
You are running a scheduled heartbeat check. Execute the following task and report your findings.
If there is nothing to report, respond with HEARTBEAT_OK.

## {section title}

{section content}
```

## Reporting

### Mattermost

- **Reporting channel**: configurable via `HEARTBEAT_CHANNEL` env var (channel ID). If not set, DM the first allowed user (the bot's configured user).
- **Top-level post**: a marker for the heartbeat cycle, e.g., `🫀 Heartbeat — 2026-03-15 10:30`
- **Threaded replies**: each section's result is posted as a reply to the top-level post
- **HEARTBEAT_OK suppression** (default: on, configurable):
  - If a section's response contains `HEARTBEAT_OK` (case-insensitive, within first 300 chars), suppress that reply
  - If ALL sections return `HEARTBEAT_OK`, delete the top-level post too (nothing to report)
  - Controlled by `HEARTBEAT_SUPPRESS_OK` env var (default: `true`). Set to `false` to see all results for debugging.

### Interactive mode

- Print each section's result to stdout with a header
- Same `HEARTBEAT_OK` suppression logic (skip printing quiet sections)

## Configuration

All via env vars (for now — advanced config deferred to backlog):

| Variable | Default | Description |
|----------|---------|-------------|
| `HEARTBEAT_INTERVAL` | `30m` | How often heartbeats run. Supports `Nm` (minutes), `Nh` (hours), or `NhNm`. Empty or `0` disables heartbeat entirely. |
| `HEARTBEAT_USER` | (empty) | Mattermost user ID to DM heartbeat reports to. |
| `HEARTBEAT_CHANNEL` | (empty) | Mattermost channel ID for reports. Overrides `HEARTBEAT_USER` if set. |
| `HEARTBEAT_SUPPRESS_OK` | `true` | Suppress sections that return HEARTBEAT_OK. Set `false` for debug. |

If neither `HEARTBEAT_USER` nor `HEARTBEAT_CHANNEL` is set in Mattermost mode, heartbeat is disabled (nowhere to report).

## Concurrency

- **Never overlap**: if a heartbeat cycle is still running when the next tick fires, skip and log a warning
- **Concurrent with user conversations**: heartbeat runs independently, different conv_id, different history
- **Sequential within a cycle**: sections run one at a time (not parallel), to keep LLM load predictable

## Timer Implementation

- `asyncio` timer task started alongside the main listen loop (Mattermost) or input loop (interactive)
- First heartbeat fires after one full interval (not immediately on startup)
- Timer respects graceful shutdown — cancel the timer task, wait for in-flight heartbeat cycle to complete
- Interval parsing: simple regex for `Nm` (minutes), `Nh` (hours), `NhNm` (combined). No external dependency.
- `HEARTBEAT_INTERVAL` set to empty string or `0` disables heartbeat entirely

## Deferred to Backlog

- **Active hours**: restrict heartbeats to specific time windows (e.g., 9am-5pm)
- **Per-section interval overrides**: some sections run hourly, others daily
- **Isolated/light context**: skip conversation history, reduce context for cost
- **Tool allowlist/blocklist via frontmatter**: restrict which tools heartbeat sections can use (also applicable to skills)
- **Scheduled tasks**: specific-time execution (cron-like), distinct from periodic heartbeat
- **`includeReasoning`**: transparency about why the agent decided to alert

## Testing

1. **HEARTBEAT.md parsing** — splits on `##` headers correctly, handles empty/missing files, merges admin + workspace
2. **Timer logic** — fires at correct interval, skips on overlap, respects shutdown
3. **Section execution** — each section gets isolated history, full tools, correct prompt format
4. **HEARTBEAT_OK detection** — case-insensitive, within first 300 chars
5. **Suppression logic** — per-section suppression, full-cycle suppression when all OK
6. **Mattermost reporting** — top-level post, threaded replies, suppression/deletion
7. **Interactive reporting** — stdout output with section headers

## Out of Scope (this session)

- Active hours / time windows
- Per-section intervals
- Tool restrictions for heartbeat
- Scheduled tasks (cron-like)
- Advanced config format (JSON/YAML)
