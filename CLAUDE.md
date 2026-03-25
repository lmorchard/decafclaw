# DecafClaw — Development Conventions

## What is this?

An AI agent testbed for exploring agent development patterns. Connects to Mattermost as a chat bot, with a web UI and interactive terminal mode as alternatives. Uses an OpenAI-compatible LLM endpoint (via LiteLLM) and Tabstack for web tools.

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
- `src/decafclaw/config_types.py` — Config sub-dataclasses (LlmConfig, MattermostConfig, etc.)
- `src/decafclaw/config_cli.py` — CLI tool for config show/get/set
- `src/decafclaw/context.py` — Forkable runtime context
- `src/decafclaw/events.py` — In-process pub/sub event bus
- `src/decafclaw/memory.py` — Memory file read/write operations
- `src/decafclaw/memory_context.py` — Proactive memory retrieval: auto-inject relevant context per turn
- `src/decafclaw/archive.py` — Conversation archive (JSONL per conversation)
- `src/decafclaw/compaction.py` — History compaction via summarization
- `src/decafclaw/embeddings.py` — Semantic search index (sqlite-vec cosine similarity)
- `src/decafclaw/todos.py` — Per-conversation to-do lists (markdown checkboxes)
- `src/decafclaw/prompts/` — System prompt assembly (SOUL.md + AGENT.md + skill catalog + loader)
- `src/decafclaw/skills/` — Skills system: discovery, parsing, catalog, bundled skills
- `src/decafclaw/skills/tabstack/` — Bundled Tabstack skill (SKILL.md + tools.py)
- `src/decafclaw/http_server.py` — HTTP server (Starlette/uvicorn): interactive button callbacks, health check
- `src/decafclaw/skills/health/` — Bundled `!health` command: agent diagnostic status
- `src/decafclaw/skills/wiki/` — Bundled wiki skill: Obsidian-compatible knowledge base, always-loaded
- `src/decafclaw/skills/dream/` — Dream consolidation: periodic memory review → wiki updates (every 3 hours)
- `src/decafclaw/skills/garden/` — Wiki gardening: structural maintenance sweep (weekly scheduled)
- `src/decafclaw/skills/claude_code/` — Claude Code subagent skill (sessions, permissions, output logging)
- `src/decafclaw/eval/` — Eval harness (YAML tests, failure reflection)
- `src/decafclaw/mcp_client.py` — MCP client: config, registry, server connections, auto-restart
- `src/decafclaw/heartbeat.py` — Heartbeat: periodic wake-up, section parsing, timer, cycle runner
- `src/decafclaw/schedules.py` — Scheduled tasks: cron-style task files, discovery, execution, timer loop
- `src/decafclaw/media.py` — Media handling: ToolResult, MediaHandler interface, workspace ref scanning
- `src/decafclaw/tools/` — Tool registry: core, memory, todo, workspace, file_share, shell, conversation, skill activation, MCP status, health, delegation
- `src/decafclaw/tools/health.py` — Health/diagnostic status tool: uptime, MCP, heartbeat, tools, embeddings
- `src/decafclaw/tools/effort_tools.py` — Effort level tool: `set_effort` for conversation model switching
- `src/decafclaw/tools/delegate.py` — Sub-agent delegation: `delegate_task` forks a child agent for a single subtask (call multiple times for parallel work)
- `src/decafclaw/tools/tool_registry.py` — Tool classification (always-loaded vs deferred), token estimation, deferred list formatting
- `src/decafclaw/tools/search_tools.py` — `tool_search` tool: keyword and exact-name lookup for deferred tools
- `src/decafclaw/reflection.py` — Self-reflection: judge call, prompt assembly, result parsing (Reflexion pattern)
- `src/decafclaw/commands.py` — User-invokable commands: trigger parsing, argument substitution, execution (fork/inline)
- `src/decafclaw/tools/confirmation.py` — Shared confirmation request helper (event-bus-based user approval)
- `src/decafclaw/runner.py` — Top-level orchestrator: manages MCP, HTTP server, Mattermost, heartbeat as parallel tasks
- `src/decafclaw/web/` — Web gateway: auth, conversations, WebSocket chat handler
- `src/decafclaw/web/static/` — Frontend: Lit web components, service layer (AuthClient, WebSocketClient, ConversationStore)

## Running

```
make run          # Interactive mode (stdin/stdout)
make dev          # Auto-restart on file changes (10s graceful shutdown)
make debug        # With debug logging
make run-pro      # With gemini-2.5-pro model
make lint         # Compile-check all source files
make typecheck    # Run pyright type checker
make check-js     # Type check JS (tsc --checkJs)
make check        # Lint + type check combined (Python + JS)
make test         # Run pytest
make vendor       # Rebuild web UI vendor bundle (npm + esbuild)
make reindex      # Rebuild embedding index from memory files
make build-eval-fixtures  # Rebuild eval embedding fixtures
make config       # Show resolved config values
```

**Important:** Only one bot instance can connect to Mattermost at a time. A second instance will silently miss websocket events. Les likely has `make dev` running in another terminal — do NOT start `make run`, `make dev`, or `make debug` without checking first. If you need to run an instance for log capture or debugging, ask Les to kill the existing one.

## Project board

Work is tracked on the [GitHub project board](https://github.com/users/lmorchard/projects/6) with columns: **Backlog**, **Ready**, **In progress**, **In review**, **Done**. Fields: Priority (P0/P1/P2), Size (XS/S/M/L/XL).

- Check **Ready** first when picking work. Consult the board at the start of each session.
- Move items to **In progress** when starting work on them.
- Move to **In review** when a PR is up.
- Move to **Done** when merged (or let GitHub auto-close via `Closes #N`).
- When filing new issues, add them to the project board with priority and size.
- When triaging or reprioritizing, update the board — it's the source of truth for what's next.

## Dev sessions

Session docs live in `.claude/dev-sessions/YYYY-MM-DD-HHMM-slug/` with `spec.md`, `plan.md`, and `notes.md`.

**Session protocol:**
1. Start session → create directory and files
2. Brainstorm → iterative Q&A to develop spec
3. **Review spec for critical gaps** — after writing the spec, always do a self-review pass: check for missing edge cases, interaction effects, and ambiguities. Fix any critical gaps found before moving to planning.
4. Plan → break into steps with prompts
5. Execute → implement, lint, test, commit per phase
6. Retro → write notes, squash, merge

## Conventions

- **Prefer clarity over abstraction.** This is an exploration project — we try to keep things simple but aren't afraid to explore complexity when a feature calls for it. Readability matters more than cleverness.
- **Files on disk, human-readable.** All agent state uses files you can read, edit, and inspect: markdown for memories and to-dos, JSONL for conversation archives, SQLite for embeddings. No opaque databases. Crash-recoverable by design.
- **Tool error returns use `ToolResult`.** Error returns should use `ToolResult(text="[error: ...]")` rather than bare strings, for consistency across all tool modules.
- **Use `asyncio.Lock` for concurrency guards.** Prefer `asyncio.Lock` over boolean flags — locks auto-release on exception, preventing stuck state.
- **Conversation state in `ConversationState` dataclass.** Per-conversation state (history, skill state, busy flag, etc.) is tracked via `ConversationState` in mattermost.py, not parallel dicts.
- **Tools receive `ctx` as first param.** All tool functions take a runtime context, even if they don't use it yet.
- **Sync vs async tools.** `execute_tool` auto-detects via `asyncio.iscoroutinefunction`. Sync tools run in `asyncio.to_thread`.
- **Tool calls run concurrently.** When the model emits multiple tool calls in one response, they execute via `asyncio.gather` with a semaphore (`max_concurrent_tools`, default 5). Each call gets a forked ctx with its own `current_tool_call_id`. All tool events carry `tool_call_id` for UI correlation.
- **Tool deferral.** When tool definitions exceed `tool_context_budget_pct` (default 10%) of `compaction_max_tokens`, non-essential tools are deferred behind `tool_search`. The model sees a name+description list and fetches full schemas on demand. Always-loaded tools configured via `ALWAYS_LOADED_TOOLS` env var. Fetched tools persist for the conversation.
- **Events for progress.** Tools publish `tool_status` events via `ctx.publish()`. The agent loop publishes `llm_start/end` and `tool_start/end`. Subscribers (Mattermost, terminal) handle display.
- **Mattermost concerns stay in `mattermost.py`.** Progress formatting, placeholder management, threading logic — all in `MattermostClient`.
- **Mattermost PATCH API quirks.** Omitting `props` from a PATCH preserves existing props (including attachments). To strip attachments, you must explicitly send `props: {"attachments": []}`. However, sending a PATCH with only `props` and no `message` field clears the message text, showing "(message deleted)". Always include the message text when patching props — fetch it first if needed.
- **Config via defaults → config.json → env vars.** Config is resolved in priority order: dataclass defaults → `data/{agent_id}/config.json` → env vars. Env vars are highest priority. Dataclass defaults in `config.py`, sub-dataclasses in `config_types.py`.
- **Use `dataclasses.replace()` to copy Config.** Never copy fields manually — new fields get silently lost. This caused a real bug with semantic search in the eval runner. For nested sub-dataclasses, use the nested pattern: `dataclasses.replace(config, agent=dataclasses.replace(config.agent, data_home=tmp, id="eval"))`.
- **Check for running bot instances before starting one.** Only one websocket connection per Mattermost bot account. A second instance silently misses events.
- **Test live in Mattermost after merging**, not just lint/pytest. Real agent behavior differs from unit tests.
- **Tool descriptions are a control surface.** Wording changes ("MUST", "NEVER", checklists, "prefer X over Y") measurably change LLM behavior. Use the eval loop to validate.
- **Group tools by noun, not verb.** `conversation_search` + `conversation_compact` in one module, not scattered across core.
- **Zero tolerance for warnings and traceback noise.** Warnings, tracebacks, and noisy error output obscure real issues. If you see them — even on shutdown, even if they're "harmless" — fix them. Catch exceptions at the right level, suppress expected cancellation errors, and keep logs clean.
- **Bug fix = test first.** When fixing a bug, first write a test that reproduces it (fails), then fix the code to make it pass. This ensures regressions are caught and documents the bug's trigger condition.
- **Commit after each logical step.** Lint and test before committing.
- **Work in a branch for iterative changes.** When making multiple related fixes (especially to UX-sensitive code like streaming/placeholder logic), work in a branch and test the full set before merging to main. Don't push rapid-fire fixes directly to main — regressions compound.
- **One agent turn per conversation at a time.** Concurrent conversations (different threads/channels) are fine.
- **Memory lives in `data/{agent_id}/workspace/memories/`.** Daily markdown files, append-only. Tools read context from ctx, not config.
- **Agent data at `data/{agent_id}/`.** Admin files (SOUL.md, AGENT.md, USER.md, COMPACTION.md, config.yaml) live at the root — read-only to the agent. Agent read/write files live in `workspace/` subdirectory.
- **System prompt from files.** SOUL.md + AGENT.md bundled in code, overridable at `data/{agent_id}/`. USER.md is workspace-only.
- **Skills are lazy-loaded (unless always-loaded).** Skill catalog (name + description) is in the system prompt. Full content and tools only load when the agent calls `activate_skill`. Per-conversation activation via `ctx.extra_tools`. Exception: skills with `always-loaded: true` in SKILL.md are auto-activated at startup — body in system prompt, tools always available, exempt from deferral.
- **Wiki is curated knowledge, memory is episodic.** Memory is append-only daily entries ("Les said X on March 13"). Wiki pages (`workspace/wiki/`) are living documents revised over time ("Les's drink preferences: Boulevardier, Old Fashioned"). The agent uses wiki for distilled facts, memory for timestamped observations. Wiki pages are Obsidian-compatible — filenames are page titles, `[[wiki-links]]` work.
- **User-invokable commands.** Skills with `user-invocable: true` can be triggered by `!name` (Mattermost) or `/name` (web UI). Supports `$ARGUMENTS`/`$0`/`$1` substitution, `context: fork` for isolated execution, and `allowed-tools` for tool pre-approval. `!help`/`/help` lists available commands.
- **Skill permissions at agent level.** `data/{agent_id}/skill_permissions.json` — outside the workspace, so the agent can't grant itself permission. User confirms activation with yes/no/always.
- **Bundled skills in `src/decafclaw/skills/`.** Each skill has SKILL.md (required) + tools.py (optional for native Python tools). Skill scan order: workspace > agent-level > bundled.
- **Skills must use absolute imports.** The skill loader uses `importlib.spec_from_file_location` without package context, so relative imports (`from .` or `from ...`) fail at runtime. Use `from decafclaw.skills.my_skill.module import ...` instead.
- **Skill config via `SkillConfig` dataclass in `tools.py`.** Skills own their config schema by exporting a `SkillConfig` dataclass. The loader resolves it at activation time via `load_sub_config` (env vars + `config.skills[name]` dict + defaults). `init(config, skill_config)` receives both the global config and the typed skill config. Skills without `SkillConfig` get the old `init(config)` signature.
- **Effort levels for model routing.** Three levels: `fast` (cheap/compliant), `default` (normal), `strong` (complex reasoning). Configured in `config.json` `models` section mapping levels to partial LLM configs. Set per-conversation via `set_effort` tool or `!think-harder`/`!think-faster`/`!think-normal` commands. Skills declare `effort` in SKILL.md frontmatter (forked contexts only). `delegate_task` accepts optional `effort` parameter. Resolved at turn start by forking config with the resolved LLM settings. Persisted in conversation sidecar.
- **MCP servers are globally available.** Configured in `data/{agent_id}/mcp_servers.json` (Claude Code compatible format). Connected eagerly on startup, tools namespaced as `mcp__<server>__<tool>`. Module-level global registry in `mcp_client.py`.
- **MCP auto-restart.** Crashed stdio servers auto-reconnect on next tool call with exponential backoff (max 3 retries). Use `mcp_status(action="restart")` for manual control.
- **Scheduled tasks via cron-style files.** Markdown files with YAML frontmatter in `data/{agent_id}/schedules/` (admin) and `workspace/schedules/` (agent-writable). Frontmatter fields: `schedule` (5-field cron), `channel` (Mattermost channel **ID** — `#name` resolution not yet implemented), `enabled`, `effort`, `allowed-tools`, `required-skills`. Independent timer loop (60s poll), per-task last-run tracking in `workspace/.schedule_last_run/`. Uses `croniter` for cron evaluation. Mattermost channel reporting not yet wired — results currently go to agent log.
- **Skill schedule frontmatter.** Skills can declare `schedule: "cron expression"` in SKILL.md to run as scheduled tasks. Only bundled and admin-level skills are honored (workspace skills cannot self-schedule). File-based schedules override skill schedules on name collision. Skills with both `schedule` and `user-invocable: true` serve as both scheduled tasks and on-demand commands.
- **Self-reflection is fail-open.** The reflection judge evaluates responses before delivery, but errors (network, parse, etc.) always pass through the response as-is. Retries consume `max_tool_iterations` budget. Skipped for child agents, cancelled turns, and empty responses.
- **Proactive memory context is fail-open.** Before each interactive turn, relevant memories/wiki are auto-injected as context. Errors silently return empty results. Skipped for heartbeat, scheduled tasks, and child agents (`skip_memory_context` flag on ctx). Requires an embedding model to be configured — silently disabled otherwise.
- **LOG_LEVEL env var.** Set `LOG_LEVEL=DEBUG` for verbose logging (default: INFO).

## Keeping docs current

Documentation lives in `docs/` — see `docs/index.md` for the full list. When adding features, new tools, config options, or architectural changes:
- **Update or add a `docs/` page** — each major feature has its own page. If you're adding a new feature, create a new doc and add it to `docs/index.md`. If you're modifying an existing feature, update its doc.
- **Update `CLAUDE.md`** — key files list, conventions, known gaps
- **Update `README.md`** — tool table, config table, project structure
- **Update `docs/context-map.md`** — if changing system prompt, tool definitions, or context assembly
- Docs should stay in sync with the code. If you change behavior, check if the docs need updating too. Stale docs are worse than no docs.

**Docs are part of the feature, not an afterthought.** When adding a new subsystem or feature, create the `docs/` page as part of the implementation PR — not as a follow-up. Same for CLAUDE.md key files and conventions.

**At the end of every dev session:**
- Review all `docs/` pages for accuracy — features built, config added, files moved.
- Update `CLAUDE.md` key files list if new modules were added.
- Backlog is what's ahead, not a history of what's done. Git history is the record.

## Known gaps

- No hard history size limit (compaction helps but unbounded archive growth)
