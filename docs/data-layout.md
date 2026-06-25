# Data Layout

DecafClaw uses a file-based data layout with a clear trust boundary between admin-managed and agent-writable files.

## Directory structure

```
data/{agent_id}/                    # Admin-level (read-only to agent)
  SOUL.md                           # Identity prompt override
  AGENT.md                          # Capabilities prompt override
  USER.md                           # Per-deployment user context
  COMPACTION.md                     # Custom compaction prompt
  REFLECTION.md                     # Custom reflection judge prompt
  MEMORY_SWEEP.md                   # Pre-compaction memory sweep prompt
  HEARTBEAT.md                      # Admin heartbeat tasks (auto-approves tools)
  config.json                       # Resolved configuration
  mcp_servers.json                  # MCP server configuration
  skill_permissions.json            # Skill activation permissions
  shell_allow_patterns.json         # Approved shell command patterns
  web_tokens.json                   # Web UI auth tokens (managed by decafclaw-token CLI)
  skills/                           # Admin-managed skills
    my-skill/SKILL.md
  schedules/                        # Admin-managed scheduled tasks
    my-task.md
  web/users/{username}/
    conversation_folders.json       # Per-user web UI folder index

  workspace/                        # Agent sandbox (read-write)
    vault/                          # Unified knowledge base (Obsidian-compatible)
      agent/
        pages/                      # Curated wiki pages (revised over time)
        journal/                    # Daily journal entries (append-only)
    conversations/                  # One directory per conversation
      {conv_id}/                    # All state for a single conversation
        archive.jsonl               # Message archive (JSONL)
        compacted.jsonl             # Pre-compaction history snapshot
        notes.md                    # Per-conversation scratchpad
        decisions.json              # Compaction decision slice
        context.json                # Per-turn context diagnostics sidecar
        canvas.json                 # Canvas panel state
        skills.json                 # Activated-skill state
        skill_data.json             # Per-skill persisted data
        vault_grants.json           # Per-conversation vault folder grants
        workflow.json               # Durable workflow journal
        uploads/                    # User-uploaded file attachments
    projects/                       # Project skill working directories
      {YYYY-MM-DD-HHMM}-{slug}/
    todos/                          # Per-conversation checklists (markdown checkboxes)
      {conv_id}.md
    skills/                         # Agent-writable skills (ClawHub installs)
    schedules/                      # Agent-writable scheduled tasks
    media/                          # Media files saved from tool results
    HEARTBEAT.md                    # Agent-managed heartbeat tasks
    embeddings.db                   # Semantic search index (SQLite + sqlite-vec)
    .schedule_last_run/             # Per-task last-run timestamps
    .heartbeat_last_run             # Heartbeat cycle tracking
    debug_context.json              # Debug dump (last debug_context call)
    debug_context_summary.txt       # Debug summary
    debug_system_prompt.md          # Debug system prompt dump
```

## Per-conversation sidecar directories

Everything belonging to a single conversation lives under one directory,
`workspace/conversations/{conv_id}/`. One listing shows all of a
conversation's state, and one `rm -rf` (or one `delete_conversation_files`
call) removes it.

`src/decafclaw/conversation_paths.py` is the single chokepoint for these
paths: `conversation_dir(config, conv_id, *, create=False)`,
`sidecar_path(config, conv_id, filename)`,
`iter_conversation_archives(config)`, and `delete_conversation_files(config,
conv_id)`, plus the `SIDECAR_FILENAMES` constant (the migration mapping).
`workflow/paths.py` delegates to it. New sidecars get a filename in this
module rather than a new flat-suffix convention.

### Migrating from the legacy flat layout

Before #576 the layout was flat — each sidecar was a sibling file named
`conversations/{conv_id}.SUFFIX` (e.g. `{conv_id}.jsonl`,
`{conv_id}.notes.md`, `{conv_id}.context.json`). The code no longer reads
that layout; an instance upgrading from it must relocate its existing
sidecars once:

`make migrate-sidecars` runs `scripts/migrate_sidecars_to_dirs.py`, which
moves flat sidecars into their per-conversation directories. It is
idempotent and never deletes data (only moves). Preview the moves first with
`make migrate-sidecars-dry`, then run `make migrate-sidecars` once after
upgrading. (There is no runtime fallback — run the migration before relying
on pre-#576 conversations.)

## Trust boundary

The key architectural decision: **admin files are read-only to the agent, workspace files are read-write.**

### Admin level (`data/{agent_id}/`)

- Prompt overrides, compaction prompt, MCP config, skill permissions, web auth tokens
- Configured by the operator, not modifiable by the agent
- Skill permissions live here so the agent can't grant itself permission to activate skills
- MCP server config lives here so the agent can't add its own tool providers
- Schedule files here are admin-authored; workspace/schedules are agent-authored (no trust inheritance)

### Workspace (`data/{agent_id}/workspace/`)

- All agent-generated state: vault, conversations, checklists, projects, embeddings, media
- File tools (`workspace_read`, `workspace_write`, etc.) are sandboxed to this directory
- Path traversal outside the workspace is rejected
- Vault lives here by default but can be relocated (e.g., to an existing Obsidian vault via `vault.vault_path`)
- Skills installed by the agent (e.g., from ClawHub) land in `workspace/skills/`
- Crash-recoverable: all files are human-readable (markdown, JSONL, JSON, SQLite)

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_HOME` | `./data` | Root directory containing agent data |
| `AGENT_ID` | `decafclaw` | Agent identifier (subdirectory name) |

The full paths are:
- Admin: `{DATA_HOME}/{AGENT_ID}/`
- Workspace: `{DATA_HOME}/{AGENT_ID}/workspace/`

## Design principles

- **Files on disk, human-readable.** Markdown for pages/journal/checklists, JSONL for conversation archives, SQLite for embeddings, JSON for config. Everything is inspectable and editable.
- **Crash-recoverable.** Append-only writes for archives and journal entries. No in-memory-only state that would be lost on crash.
- **One agent, one directory.** All state for an agent instance lives under `data/{agent_id}/`. Multiple agents can coexist by using different IDs.
- **Obsidian-friendly vault.** The vault uses standard markdown with `[[wiki-links]]` and optional YAML frontmatter — compatible with Obsidian, pointed at a synced vault via config if desired.
