# DecafClaw — Development Conventions

## What is this?

A minimal AI agent for learning how agent frameworks work. Connects to Mattermost as a chat bot, with an interactive terminal mode as fallback. Uses an OpenAI-compatible LLM endpoint (via LiteLLM) and Tabstack for web tools.

## Architecture

- **Event-driven**: EventBus pub/sub (`events.py`) decouples tool execution from message delivery
- **Runtime context**: Go-inspired forkable Context (`context.py`) carries config + event bus, forked per-request
- **Async throughout**: Agent loop, LLM client, and tool execution are all async. Streaming Tabstack tools use `AsyncTabstack`.
- **Per-conversation state**: Threads and top-level channel messages are independent conversations, keyed by `root_id` (threads) or `channel_id` (top-level)

## Key files

- `src/decafclaw/__init__.py` — Entry point, config/context setup, mode dispatch
- `src/decafclaw/agent.py` — Agent loop + interactive mode
- `src/decafclaw/mattermost.py` — Mattermost client, message handling, flood protection, progress subscriber
- `src/decafclaw/llm.py` — LLM client (OpenAI-compatible)
- `src/decafclaw/config.py` — Dataclass config from env vars / .env
- `src/decafclaw/context.py` — Forkable runtime context
- `src/decafclaw/events.py` — In-process pub/sub event bus
- `src/decafclaw/memory.py` — Memory file read/write operations
- `src/decafclaw/archive.py` — Conversation archive (JSONL per conversation)
- `src/decafclaw/compaction.py` — History compaction via summarization
- `src/decafclaw/tools/` — Tool registry, core tools, Tabstack tools, memory tools

## Running

```
make run          # Interactive mode (stdin/stdout)
make dev          # Auto-restart on file changes (needs uv sync --extra dev)
make debug        # With debug logging (NOTE: LOG_LEVEL env var not yet wired up)
make run-pro      # With gemini-2.5-pro model
make lint         # Compile-check all source files
make test         # Import smoke tests
```

**Important:** Only one bot instance can connect to Mattermost at a time. A second instance will silently miss websocket events. Les likely has `make dev` running in another terminal — do NOT start `make run`, `make dev`, or `make debug` without checking first. If you need to run an instance for log capture or debugging, ask Les to kill the existing one.

## Dev sessions

Session docs live in `.claude/dev-sessions/YYYY-MM-DD-HHMM-slug/` with `spec.md`, `plan.md`, and `notes.md`.

**Session protocol:**
1. Start session → create directory and files
2. Brainstorm → iterative Q&A to develop spec
3. **Review spec for critical gaps** — always do a review pass before planning
4. Plan → break into steps with prompts
5. Execute → implement, lint, test, commit per phase
6. Retro → write notes, squash, merge

## Conventions

- **Keep it simple.** This is a learning project. Prefer clarity over abstraction.
- **Tools receive `ctx` as first param.** All tool functions take a runtime context, even if they don't use it yet.
- **Sync vs async tools.** `execute_tool` auto-detects via `asyncio.iscoroutinefunction`. Sync tools run in `asyncio.to_thread`.
- **Events for progress.** Tools publish `tool_status` events via `ctx.publish()`. The agent loop publishes `llm_start/end` and `tool_start/end`. Subscribers (Mattermost, terminal) handle display.
- **Mattermost concerns stay in `mattermost.py`.** Progress formatting, placeholder management, threading logic — all in `MattermostClient`.
- **Config via env vars.** All config comes from `.env` / environment. Dataclass defaults in `config.py`.
- **Commit after each logical step.** Lint and test before committing.
- **One agent turn per conversation at a time.** Concurrent conversations (different threads/channels) are fine.
- **Memory lives in `data/workspace/{agent_id}/memories/`.** Daily markdown files per user, append-only. Tools read user_id/channel/thread from context, not config.
- **Agent workspace at `data/workspace/{agent_id}/`.** Configurable via `DATA_HOME` and `AGENT_ID`. Agent-owned files (memories, future to-do lists) go here.
- **System prompt from file.** If `data/workspace/{agent_id}/SYSTEM_PROMPT.md` exists, it overrides the default system prompt. Edit and restart to iterate.
- **LOG_LEVEL env var.** Set `LOG_LEVEL=DEBUG` for verbose logging (default: INFO).

## Keeping docs current

When adding features, new tools, config options, or architectural changes:
- **Update `README.md`** — tool table, config table, architecture diagram, project structure
- **Update `CLAUDE.md`** — conventions, key files, known gaps
- These should stay in sync with the actual codebase. If you change code, check if the docs need updating too.

## Known gaps

- No history truncation (unbounded growth — compaction helps but no hard limit)
