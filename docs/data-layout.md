# Data Layout

DecafClaw uses a file-based data layout with a clear trust boundary between admin-managed and agent-writable files.

## Directory structure

```
data/{agent_id}/                    # Admin-level (read-only to agent)
  SOUL.md                           # Identity prompt override
  AGENT.md                          # Capabilities prompt override
  USER.md                           # Per-deployment user context
  COMPACTION.md                     # Custom compaction prompt
  HEARTBEAT.md                      # Admin heartbeat tasks (auto-approves tools)
  skill_permissions.json            # Skill activation permissions
  mcp_servers.json                  # MCP server configuration
  skills/                           # Admin-managed skills
    my-skill/
      SKILL.md

  workspace/                        # Agent sandbox (read-write)
    memories/                       # Persistent memory (daily markdown)
      2026/
        2026-03-15.md
    conversations/                  # Conversation archives (JSONL)
      {conv_id}.jsonl
    todos/                          # Per-conversation to-do lists
      {conv_id}.md
    skills/                         # Agent-writable skills (ClawHub installs)
      weather/
        SKILL.md
    media/                          # Media files saved from tool results
      mcp-image-1.png
    HEARTBEAT.md                    # Agent-managed heartbeat tasks
    embeddings.db                   # Semantic search index (SQLite)
    debug_context.json              # Debug dump (last debug_context call)
    debug_context_summary.txt       # Debug summary
    debug_system_prompt.md          # Debug system prompt dump
```

## Trust boundary

The key architectural decision: **admin files are read-only to the agent, workspace files are read-write.**

### Admin level (`data/{agent_id}/`)

- Prompt overrides, compaction prompt, MCP config, skill permissions
- Configured by the operator, not modifiable by the agent
- Skill permissions live here so the agent can't grant itself permission to activate skills
- MCP server config lives here so the agent can't add its own tool providers

### Workspace (`data/{agent_id}/workspace/`)

- All agent-generated state: memories, conversations, todos, embeddings
- File tools (`workspace_read`, `workspace_write`, `workspace_list`) are sandboxed to this directory
- Path traversal outside the workspace is rejected
- Skills installed by the agent (e.g., from ClawHub) land here
- Crash-recoverable: all files are human-readable (markdown, JSONL, SQLite)

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_HOME` | `./data` | Root directory containing agent data |
| `AGENT_ID` | `decafclaw` | Agent identifier (subdirectory name) |

The full paths are:
- Admin: `{DATA_HOME}/{AGENT_ID}/`
- Workspace: `{DATA_HOME}/{AGENT_ID}/workspace/`

## Design principles

- **Files on disk, human-readable.** Markdown for memories and todos, JSONL for conversation archives, SQLite for embeddings, JSON for config. Everything is inspectable and editable.
- **Crash-recoverable.** Append-only writes for archives and memories. No in-memory-only state that would be lost on crash.
- **One agent, one directory.** All state for an agent instance lives under `data/{agent_id}/`. Multiple agents can coexist by using different IDs.
