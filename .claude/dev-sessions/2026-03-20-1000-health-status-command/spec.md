# Spec: Health/Status Diagnostic Command

**Issue:** #88
**Branch:** `health-status-command`

## Problem

There's no way to quickly check the agent's operational state — MCP connections, heartbeat timing, tool budget, embedding index size, or basic process health. When something's off, you have to dig through logs or poke individual subsystems.

## Solution

A `health_status` tool that gathers diagnostics from all major subsystems and returns a pre-formatted markdown report, plus a `!health` user-invokable command that tells the agent to call it.

## Components

### 1. Tool: `health_status`

**Module:** `src/decafclaw/tools/health.py`

**Signature:** `tool_health_status(ctx) -> str | ToolResult`

Returns `str` on success, `ToolResult` with `[error: ...]` on total failure (per project convention). Individual subsection failures are rendered inline as error lines rather than failing the whole tool.

**Returns:** Markdown report with these sections:

#### Process
- Uptime (human-readable, e.g. "2h 14m 32s") — delta from a module-level `_start_time = time.monotonic()` captured at import
- RSS memory usage via `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`
  - macOS (`sys.platform == "darwin"`): value is in **bytes**, divide by `1024 * 1024` for MB
  - Linux: value is in **kilobytes**, divide by `1024` for MB

#### MCP Servers
- Per server: name, status (connected/disconnected/failed), tool count, retry count
- If no servers configured or registry unavailable, say so

#### Heartbeat
- Enabled/disabled (based on whether interval is set)
- If enabled: interval, last run (absolute timestamp + relative "3m ago"), next due ("in 2m" or "overdue by 1m")
- Data from `parse_interval()` and heartbeat last-run file
- Note: `_read_last_heartbeat()` is currently private — rename to `read_last_heartbeat()` to make it a public API since health.py has a legitimate cross-module need

#### Tools
- Active tool count, deferred tool count
- Estimated tool token usage and budget
- Data from `classify_tools()` and `estimate_tool_tokens()`

#### Embeddings
- Total entries in the index
- Breakdown by source type (memory vs conversation)
- SQLite COUNT queries on `memory_embeddings` table
- If no database exists, say so
- If database is locked or corrupted, render as "Embeddings: [error reading database]" rather than failing the tool

**Error handling:** Each section is gathered independently. If one subsystem fails (exception, missing data), that section shows an error line and the rest of the report still renders.

**Classification:** Deferred tool (not in `ALWAYS_LOADED_TOOLS`). The `!health` skill pre-approves it via `allowed-tools`, so it loads automatically when the command is invoked. Also discoverable via `tool_search` — the tool description should include keywords: health, status, diagnostic, uptime, mcp.

**Registration:** `HEALTH_TOOLS` dict + `HEALTH_TOOL_DEFINITIONS` list, following the pattern in `mcp_tools.py` and `heartbeat_tools.py`.

**Integration:** Import and merge into `src/decafclaw/tools/__init__.py` alongside existing tool modules.

### 2. Command: `!health`

**Skill directory:** `src/decafclaw/skills/health/`

**SKILL.md frontmatter:**
```yaml
name: health
description: Show agent diagnostic status
user-invocable: true
context: inline
allowed-tools:
  - health_status
```

**Body:** Instructs the agent to call the `health_status` tool and share the full output with the user.

## Example Output

```
## Agent Health

### Process
- **Uptime:** 2h 14m 32s
- **Memory (RSS):** 87.3 MB

### MCP Servers
| Server | Status | Tools | Retries |
|--------|--------|-------|---------|
| tabstack | connected | 12 | 0 |
| playwright | failed | 0 | 3 |

### Heartbeat
- **Status:** enabled
- **Interval:** 30m
- **Last run:** 2026-03-20 09:45:00 (15m ago)
- **Next due:** in 15m

### Tools
- **Active:** 14 | **Deferred:** 23
- **Token usage:** ~1,200 / 4,000 budget

### Embeddings
- **Total entries:** 342
- **Memory:** 287 | **Conversation:** 55
```

## Out of Scope

- Conversation queue depth (requires reaching into MattermostClient internals)
- Recent errors/warnings from logs (needs a log ring buffer — new infrastructure)
- Per-subsystem restart controls (MCP already has `mcp_status(action="restart")`)

## Acceptance Criteria

- `!health` in Mattermost or `/health` in web UI produces a readable diagnostic report
- The agent can call `health_status` on its own (e.g. during troubleshooting)
- All five sections render correctly when their respective subsystems are active
- Graceful handling when subsystems are absent (no MCP servers, no embeddings DB, heartbeat disabled)
- Individual section errors don't crash the whole report
- Tool is deferred by default, loads on demand via skill activation or `tool_search`
- Tests cover the tool function with mocked subsystem state
