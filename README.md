# DecafClaw

A minimal AI agent in Python. Built to understand how agent frameworks work by stripping away all the complexity.

## What it does

Connects to Mattermost as a chat bot (or runs in terminal mode), calls an LLM with tool-calling, executes tools, and responds. Streams responses as they arrive.

**Features:**
- **[Skills](docs/skills.md)** — portable tool packages following the [Agent Skills](https://agentskills.io) standard
- **[MCP servers](docs/mcp-servers.md)** — connect external tools via the Model Context Protocol (stdio + HTTP)
- **[Heartbeat](docs/heartbeat.md)** — periodic agent tasks with threaded Mattermost reporting
- **[Interactive buttons](docs/http-server.md)** — Mattermost button confirmations with HTTP callback server
- **[File attachments](docs/file-attachments.md)** — upload images, files, and rich media to Mattermost
- **[Streaming](docs/streaming.md)** — token-by-token response streaming with throttled edits
- **[Memory](docs/memory.md)** — persistent file-based memory with semantic search
- **[Conversations](docs/conversations.md)** — archive, resume, and auto-compaction
- **[Eval loop](docs/eval-loop.md)** — test prompts and tools with real LLM calls
- **[Deployment](docs/deployment.md)** — systemd service on a Debian VM

## Quick start

```bash
# Clone and install
git clone https://github.com/lmorchard/decafclaw.git
cd decafclaw
uv sync

# Configure
cp .env.example .env
# Edit .env with your LLM endpoint and optional Mattermost keys
# Or use data/{agent_id}/config.json — env vars take priority

# Run interactively (no Mattermost needed)
make run

# Or run as a Mattermost bot
uv run decafclaw
```

See [docs/installation.md](docs/installation.md) for full setup and configuration reference.

## Built-in tools

| Tool | What it does |
|------|-------------|
| `web_fetch` | Fetch raw HTML from a URL |
| `think` | Internal reasoning scratchpad (hidden from user) |
| `current_time` | Get current date and time |
| `debug_context` | Dump context as JSON file attachments |
| `context_stats` | Token budget breakdown and diagnostics |
| `memory_save` | Save a persistent memory with tags |
| `memory_search` | Search memories (semantic or substring) |
| `memory_recent` | Recall recent memories |
| `conversation_search` | Search past conversations semantically |
| `conversation_compact` | Manually trigger conversation compaction |
| `todo_add/complete/list/clear` | Per-conversation to-do lists |
| `workspace_read/write/list` | Sandboxed file operations (read supports line ranges) |
| `workspace_edit` | Exact string replacement in workspace files |
| `workspace_insert` | Insert text at a specific line number |
| `workspace_replace_lines` | Replace or delete a range of lines |
| `workspace_append` | Append content to a file |
| `workspace_search` | Regex search across workspace files |
| `workspace_glob` | Find files by name/glob pattern |
| `workspace_move` | Move or rename a file within the workspace |
| `workspace_delete` | Delete a file from the workspace |
| `workspace_diff` | Unified diff between two workspace files |
| `file_share` | Share workspace files as Mattermost attachments |
| `shell` | Run shell commands (requires user confirmation) |
| `activate_skill` | Load a skill's tools into the conversation |
| `refresh_skills` | Re-scan skill directories |
| `mcp_status` | Show/restart MCP server connections |
| `heartbeat_trigger` | Manually fire a heartbeat cycle |
| `delegate_task` | Delegate a subtask to a child agent (call multiple times for parallel work) |

Skills and MCP servers provide additional tools on demand.

## Architecture

```
User message → Build prompt (SOUL.md + AGENT.md + skill catalog + history + tools)
                    ↓
               Call LLM (streaming or all-at-once)
                    ↓
            ┌── Tool calls? ──→ Execute tools → Publish events → Loop back
            │                         ↑
            │                    Event bus notifies subscribers
            │                    (Mattermost edits placeholder,
            │                     terminal prints progress)
            │
            └── Text response → Process media → Send to user
                                     ↓
                              Archive + maybe compact
```

See [docs/](docs/index.md) for detailed documentation on each feature, [docs/data-layout.md](docs/data-layout.md) for file structure, and [docs/context-map.md](docs/context-map.md) for prompt assembly.

## Development

```bash
make dev          # Auto-restart on file changes
make debug        # With debug logging
make test         # Run pytest
make lint         # Ruff linting
make typecheck    # Pyright type checking
make check-js     # TypeScript/JSDoc type checking
make check        # Lint + type check (Python + JS)
make vendor       # Rebuild web UI vendor bundle
make lint-fix     # Auto-fix lint issues
make fmt          # Format with ruff
make config       # Show resolved config values
```

## What this is NOT

This is not a framework. It's a learning project — built to understand how tools like OpenClaw, nanobot, and picoclaw work under the hood. The code is intentionally simple, with minimal abstractions.

## License

MIT
