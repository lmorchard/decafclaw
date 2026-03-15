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
- `src/decafclaw/embeddings.py` — Semantic search index (SQLite + cosine similarity)
- `src/decafclaw/todos.py` — Per-conversation to-do lists (markdown checkboxes)
- `src/decafclaw/prompts/` — System prompt assembly (SOUL.md + AGENT.md + skill catalog + loader)
- `src/decafclaw/skills/` — Skills system: discovery, parsing, catalog, bundled skills
- `src/decafclaw/skills/tabstack/` — Bundled Tabstack skill (SKILL.md + tools.py)
- `src/decafclaw/eval/` — Eval harness (YAML tests, failure reflection)
- `src/decafclaw/mcp_client.py` — MCP client: config, registry, server connections, auto-restart
- `src/decafclaw/tools/` — Tool registry: core, memory, todo, workspace, shell, conversation, skill activation, MCP status

## Running

```
make run          # Interactive mode (stdin/stdout)
make dev          # Auto-restart on file changes (10s graceful shutdown)
make debug        # With debug logging
make run-pro      # With gemini-2.5-pro model
make lint         # Compile-check all source files
make test         # Run pytest (128 tests)
make reindex      # Rebuild embedding index from memory files
make build-eval-fixtures  # Rebuild eval embedding fixtures
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
- **Files on disk, human-readable.** All agent state uses files you can read, edit, and inspect: markdown for memories and to-dos, JSONL for conversation archives, SQLite for embeddings. No opaque databases. Crash-recoverable by design.
- **Tools receive `ctx` as first param.** All tool functions take a runtime context, even if they don't use it yet.
- **Sync vs async tools.** `execute_tool` auto-detects via `asyncio.iscoroutinefunction`. Sync tools run in `asyncio.to_thread`.
- **Events for progress.** Tools publish `tool_status` events via `ctx.publish()`. The agent loop publishes `llm_start/end` and `tool_start/end`. Subscribers (Mattermost, terminal) handle display.
- **Mattermost concerns stay in `mattermost.py`.** Progress formatting, placeholder management, threading logic — all in `MattermostClient`.
- **Config via env vars.** All config comes from `.env` / environment. Dataclass defaults in `config.py`.
- **Use `dataclasses.replace()` to copy Config.** Never copy fields manually — new fields get silently lost. This caused a real bug with semantic search in the eval runner.
- **Check for running bot instances before starting one.** Only one websocket connection per Mattermost bot account. A second instance silently misses events.
- **Test live in Mattermost after merging**, not just lint/pytest. Real agent behavior differs from unit tests.
- **Tool descriptions are a control surface.** Wording changes ("MUST", "NEVER", checklists, "prefer X over Y") measurably change LLM behavior. Use the eval loop to validate.
- **Group tools by noun, not verb.** `conversation_search` + `conversation_compact` in one module, not scattered across core.
- **Commit after each logical step.** Lint and test before committing.
- **One agent turn per conversation at a time.** Concurrent conversations (different threads/channels) are fine.
- **Memory lives in `data/{agent_id}/workspace/memories/`.** Daily markdown files, append-only. Tools read context from ctx, not config.
- **Agent data at `data/{agent_id}/`.** Admin files (SOUL.md, AGENT.md, USER.md, COMPACTION.md, config.yaml) live at the root — read-only to the agent. Agent read/write files live in `workspace/` subdirectory.
- **System prompt from files.** SOUL.md + AGENT.md bundled in code, overridable at `data/{agent_id}/`. USER.md is workspace-only.
- **Skills are lazy-loaded.** Skill catalog (name + description) is in the system prompt. Full content and tools only load when the agent calls `activate_skill`. Per-conversation activation via `ctx.extra_tools`.
- **Skill permissions at agent level.** `data/{agent_id}/skill_permissions.json` — outside the workspace, so the agent can't grant itself permission. User confirms activation with yes/no/always.
- **Bundled skills in `src/decafclaw/skills/`.** Each skill has SKILL.md (required) + tools.py (optional for native Python tools). Skill scan order: workspace > agent-level > bundled.
- **MCP servers are globally available.** Configured in `data/{agent_id}/mcp_servers.json` (Claude Code compatible format). Connected eagerly on startup, tools namespaced as `mcp__<server>__<tool>`. Module-level global registry in `mcp_client.py`.
- **MCP auto-restart.** Crashed stdio servers auto-reconnect on next tool call with exponential backoff (max 3 retries). Use `mcp_status(action="restart")` for manual control.
- **LOG_LEVEL env var.** Set `LOG_LEVEL=DEBUG` for verbose logging (default: INFO).

## Keeping docs current

Documentation lives in `docs/` — see `docs/index.md` for the full list. When adding features, new tools, config options, or architectural changes:
- **Update or add a `docs/` page** — each major feature has its own page. If you're adding a new feature, create a new doc and add it to `docs/index.md`. If you're modifying an existing feature, update its doc.
- **Update `CLAUDE.md`** — key files list, conventions, known gaps
- **Update `README.md`** — tool table, config table, project structure
- **Update `docs/context-map.md`** — if changing system prompt, tool definitions, or context assembly
- Docs should stay in sync with the code. If you change behavior, check if the docs need updating too. Stale docs are worse than no docs.

## Known gaps

- No hard history size limit (compaction helps but unbounded archive growth)
