# Heartbeat

DecafClaw can periodically wake up, read HEARTBEAT.md files, and perform the tasks described within. This enables proactive monitoring, recurring checks, and one-off tasks without user interaction.

Inspired by [OpenClaw's heartbeat](https://docs.openclaw.ai/gateway/heartbeat), with improvements: sections split on `##` headers run as independent concurrent agent turns for more reliable execution, and admin-authored sections auto-approve skill activation and shell commands.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HEARTBEAT_INTERVAL` | `30m` | How often heartbeats run. `Nm` (minutes), `Nh` (hours), `NhNm`, or plain seconds. Empty or `0` disables entirely. |
| `HEARTBEAT_USER` | (empty) | Mattermost user ID to DM reports to. |
| `HEARTBEAT_CHANNEL` | (empty) | Mattermost channel ID for reports. Overrides `HEARTBEAT_USER`. |
| `HEARTBEAT_SUPPRESS_OK` | `true` | Suppress verbose output for sections that return HEARTBEAT_OK. |

### Reporting is optional

Heartbeat runs even when `HEARTBEAT_USER` and `HEARTBEAT_CHANNEL` are both empty — it just skips Mattermost posting. Tasks still execute (files are written, tools are called, etc.). Set a channel or user to see reports.

## HEARTBEAT.md

Plain markdown files with tasks for the agent to execute on each heartbeat cycle.

### File locations

Two files are merged at runtime:

| Location | Writable by agent | Auto-approve tools | Purpose |
|----------|-------------------|--------------------|---------|
| `data/{agent_id}/HEARTBEAT.md` | No | Yes | Admin-managed recurring tasks |
| `data/{agent_id}/workspace/HEARTBEAT.md` | Yes | No | Agent-managed tasks (one-offs, self-scheduled) |

Admin sections run first, then workspace sections.

### Trust boundary

Sections from the admin HEARTBEAT.md auto-approve skill activation and shell commands — the admin authored the prompts, so they're trusted. Sections from the workspace HEARTBEAT.md do **not** auto-approve, since the agent can write to that file. This prevents the agent from granting itself tool access.

### Format

Use `##` headers to separate tasks. Each section runs as an independent agent turn with its own isolated context:

```markdown
## Check deploy status

Look up the current deploy pipeline status. Report if anything is stuck or failing.

## RSS feed check

Check the following RSS feeds for new posts since last check:
- https://example.com/feed.xml
Use workspace to store the last-seen timestamps.

## One-off: verify DNS migration

Check that example.com resolves to 1.2.3.4. If it does, report success
and remove this section from the workspace HEARTBEAT.md.
```

Content before the first `##` is treated as its own section, so a simple checklist with no headers works too (OpenClaw compatibility):

```markdown
- Check if the website is up
- Look for new emails
- Summarize any alerts
```

### Why sections?

Each `##` section gets its own agent turn with fresh history. This means:
- Every directive gets the LLM's full attention
- One failing section doesn't affect the rest
- Sections run concurrently for faster heartbeat cycles
- Results are reported per-section

## HEARTBEAT_OK

If a section has nothing to report, the agent should respond with `HEARTBEAT_OK` (case-insensitive, within the first 300 characters). When `HEARTBEAT_SUPPRESS_OK` is `true` (default):

- OK sections show as `✅ Title — OK` in the thread (compact)
- Non-OK sections show the full response
- If ALL sections return OK, the marker is edited to "❤️ Heartbeat — time — all OK"

Set `HEARTBEAT_SUPPRESS_OK=false` to see full output for all sections.

## Mattermost reporting

Each heartbeat cycle creates a thread:

1. **Marker posted immediately**: `❤️ Heartbeat — 2026-03-15 10:30 (3 section(s))`
2. **Placeholder per section**: `⏳ Title — running...` (all posted at once)
3. **As sections complete**: placeholders edited to show results or `✅ OK`
4. **Marker updated**: edited to `❤️ Heartbeat — time — all OK` or `— done`

This gives real-time visibility into which sections are still running.

## Manual trigger

Ask the agent to "trigger heartbeat" or "run heartbeat" — it calls the `heartbeat_trigger` tool which fires a cycle immediately without waiting for the timer. Results are posted to the configured channel (if set), and the tool returns immediately without blocking the conversation.

## Overlap protection

If a heartbeat cycle takes longer than the interval, the next tick is skipped and a warning is logged. This prevents runaway concurrent cycles. If you see overlap warnings, your HEARTBEAT.md tasks are too heavy for the configured interval.

## Tool access

Heartbeat sections have the full tool set available. The agent prefers workspace tools (`workspace_read`, `workspace_write`, `workspace_list`) over shell commands, and uses `current_time` instead of `date` via shell.

For admin HEARTBEAT.md sections, skill activation and shell commands are auto-approved. For workspace sections, normal confirmation rules apply (which means confirmation-requiring tools will time out in heartbeat — avoid them in workspace HEARTBEAT.md).

## Examples

### Weather check (requires weather skill)

```markdown
## Weather alert

Activate the weather skill and check the forecast for Portland, OR.
Report only if there are severe weather warnings or temperatures
below 20F or above 100F.
```

### Cat fact of the day

```markdown
## Cat fact

Read cat-facts.txt from workspace. Create a file named
hbtest-{current date and time}.txt and write an interesting cat fact.
```

### Math check (always reports)

```markdown
## Math check

What is 2 + 2? Report the answer. Do NOT respond with HEARTBEAT_OK.
```

### Self-removing one-off

The agent can edit its own workspace HEARTBEAT.md to remove completed one-off tasks:

```markdown
## One-off: Check DNS propagation

Verify that newsite.example.com resolves to 203.0.113.42.
If confirmed, report success and use workspace_write to remove this section
from workspace/HEARTBEAT.md.
```

### Silent task (no report needed)

```markdown
## Rotate logs

Use workspace_list to check for log files older than 7 days.
If found, delete them. Respond with HEARTBEAT_OK.
```
