# DecafClaw

An AI agent testbed in Python. Built to explore agent development patterns — tool calling, memory, reflection, skills, multi-model routing, and more. Increasingly focused on personal knowledge management and writing tools, with an Obsidian-like shared vault where user and agent collaborate on markdown documents.

## What it does

Multi-channel AI agent with a shared knowledge vault. Connects to Mattermost as a chat bot, runs in a web UI with WYSIWYG wiki editing, or runs in terminal mode. Multi-provider LLM support (Vertex/Gemini, OpenAI, OpenAI-compatible) with named model configs and per-conversation model selection. Streams responses as they arrive.

**Key features:** [Web UI](docs/web-ui.md) | [Skills](docs/skills.md) | [MCP servers](docs/mcp-servers.md) | [Vault & memory](docs/vault.md) | [Conversations](docs/conversations.md) | [Streaming](docs/streaming.md) | [Heartbeat](docs/heartbeat.md) | [Scheduled tasks](docs/schedules.md) | [Sub-agent delegation](docs/delegation.md) | [Eval loop](docs/eval-loop.md) | [Self-reflection](docs/reflection.md) | [Notifications](docs/notifications.md)

See [docs/](docs/index.md) for the full feature list.

## Quick start

```bash
git clone https://github.com/lmorchard/decafclaw.git
cd decafclaw
uv sync
cp .env.example .env    # then configure LLM provider — see docs/providers.md
make run                 # interactive terminal mode (no Mattermost needed)
```

See [Installation & Setup](docs/installation.md) for provider configuration, Mattermost bot setup, and all options.

## Architecture

```
Mattermost / Web UI / Terminal
            ↓
       Agent turn (agent.py)
            ↓
  ContextComposer assembles prompt
  (system prompt + vault context + history + tools)
            ↓
       Call LLM ←───────────────────┐
            ↓                       │
    Tool calls? ── yes → Execute → Loop back
            │
            no
            ↓
    Text response → Archive + maybe compact
```

All state is [files on disk](docs/data-layout.md) — markdown, JSONL, SQLite. See [Context Composer](docs/context-composer.md) for prompt assembly details.

## Development

```bash
make dev       # Auto-restart on file changes
make test      # Run pytest
make check     # Lint + type check (Python + JS)
make vendor    # Rebuild web UI vendor bundle
make config    # Show resolved config values
```

## What this is NOT

This is not a framework. It's a learning project — built to understand how tools like OpenClaw, nanobot, and picoclaw work under the hood. The code is intentionally simple, with minimal abstractions.

## License

MIT
