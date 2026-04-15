# DecafClaw — Development Conventions

## What is this?

An AI agent testbed for exploring agent development patterns. Connects to Mattermost as a chat bot, with a web UI and interactive terminal mode as alternatives. Multi-provider LLM support (Vertex/Gemini, OpenAI, LiteLLM-compat) with named model configs. Tabstack for web tools.

## Architecture

- **Event-driven**: EventBus pub/sub (`events.py`) decouples tool execution from message delivery
- **Runtime context**: Go-inspired forkable Context (`context.py`) carries config + event bus, forked per-request
- **Async throughout**: Agent loop, LLM client, and tool execution are all async. Streaming Tabstack tools use `AsyncTabstack`.
- **Per-conversation state**: Threads and top-level channel messages are independent conversations, keyed by `root_id` (threads) or `channel_id` (top-level)

## Key files

- `src/decafclaw/__init__.py` — Entry point, config/context setup, mode dispatch
- `src/decafclaw/agent.py` — Agent loop: turn orchestration, tool execution, LLM calls
- `src/decafclaw/interactive_terminal.py` — Interactive terminal mode (stdin/stdout REPL)
- `src/decafclaw/conversation_manager.py` — Central orchestrator: agent loop lifecycle, confirmation persistence, per-conversation event streams
- `src/decafclaw/confirmations.py` — Confirmation types (ConfirmationAction, Request, Response), handler registry
- `src/decafclaw/mattermost.py` — Mattermost transport adapter: message handling, debouncing, circuit breaker, ConversationDisplay lifecycle
- `src/decafclaw/mattermost_display.py` — ConversationDisplay: per-turn Mattermost message sequencing
- `src/decafclaw/llm/` — LLM client package: provider abstraction, registry, multi-provider support
- `src/decafclaw/llm/types.py` — Provider protocol, StreamCallback type
- `src/decafclaw/llm/registry.py` — Provider registry: init, lookup, lifecycle
- `src/decafclaw/llm/providers/openai_compat.py` — OpenAI-compatible provider (httpx + SSE): LiteLLM, Ollama, vLLM, OpenRouter
- `src/decafclaw/llm/providers/openai.py` — Direct OpenAI API provider
- `src/decafclaw/llm/providers/vertex.py` — Vertex AI Gemini provider (native REST + ADC auth)
- `src/decafclaw/config.py` — Dataclass config from env vars / .env
- `src/decafclaw/config_types.py` — Config sub-dataclasses (ProviderConfig, ModelConfig, LlmConfig, MattermostConfig, etc.)
- `src/decafclaw/config_cli.py` — CLI tool for config show/get/set
- `src/decafclaw/context.py` — Forkable runtime context with sub-objects: TokenUsage, ToolState, SkillState, ComposerState
- `src/decafclaw/context_composer.py` — Context composer: unified context assembly, relevance scoring, dynamic budget allocation
- `src/decafclaw/frontmatter.py` — YAML frontmatter parsing/serialization for vault pages (summary, keywords, tags, importance)
- `src/decafclaw/events.py` — In-process pub/sub event bus
- `src/decafclaw/memory_context.py` — Vault retrieval: embedding search, graph expansion, metadata enrichment
- `src/decafclaw/archive.py` — Conversation archive (JSONL per conversation)
- `src/decafclaw/compaction.py` — History compaction via summarization, pre-compaction memory sweep
- `src/decafclaw/embeddings.py` — Semantic search index (sqlite-vec cosine similarity)
- `src/decafclaw/checklist.py` — Per-conversation checklist execution loop (markdown checkboxes)
- `src/decafclaw/tools/checklist_tools.py` — Checklist tools: create, step_done, abort, status (always-loaded)
- `src/decafclaw/prompts/` — System prompt assembly (SOUL.md + AGENT.md + skill catalog + loader)
- `src/decafclaw/skills/` — Skills system: discovery, parsing, catalog, bundled skills
- `src/decafclaw/skills/tabstack/` — Bundled Tabstack skill (SKILL.md + tools.py)
- `src/decafclaw/http_server.py` — HTTP server (Starlette/uvicorn): interactive button callbacks, health check
- `src/decafclaw/skills/health/` — Bundled `!health` command: agent diagnostic status
- `src/decafclaw/skills/vault/` — Bundled vault skill: unified knowledge base (pages + journal), always-loaded
- `src/decafclaw/skills/dream/` — Dream consolidation: periodic journal review → vault page updates (every 3 hours)
- `src/decafclaw/skills/garden/` — Vault gardening: structural maintenance sweep (weekly scheduled)
- `src/decafclaw/skills/claude_code/` — Claude Code subagent skill (sessions, permissions, output logging)
- `src/decafclaw/skills/project/` — Project workflow skill: state machine (`state.py`), plan parser (`plan_parser.py`), lifecycle tools, dynamic tool loading (`get_tools`), `end_turn` for phase boundaries
- `src/decafclaw/eval/` — Eval harness (YAML tests, failure reflection)
- `src/decafclaw/mcp_client.py` — MCP client: config, registry, server connections, auto-restart
- `src/decafclaw/heartbeat.py` — Heartbeat: periodic wake-up, section parsing, timer, cycle runner
- `src/decafclaw/schedules.py` — Scheduled tasks: cron-style task files, discovery, execution, timer loop
- `src/decafclaw/media.py` — Media handling: ToolResult, MediaSaveResult, MediaHandler interface (LocalFile/Mattermost), workspace ref scanning
- `src/decafclaw/util.py` — Shared utilities (estimate_tokens)
- `src/decafclaw/polling.py` — Shared polling loop and task preamble builder (used by heartbeat + schedules)
- `src/decafclaw/tools/` — Tool registry: core, todo, workspace, file_share, shell, background processes, conversation, skill activation, MCP status, health, delegation
- `src/decafclaw/tools/background_tools.py` — Background process management: start, status, stop, list long-running processes (servers, watchers)
- `src/decafclaw/tools/http_tools.py` — General-purpose HTTP request tool: all methods, headers, body, URL-based allowlist
- `src/decafclaw/tools/health.py` — Health/diagnostic status tool: uptime, MCP, heartbeat, tools, embeddings
- `src/decafclaw/tools/model_tools.py` — Model selection tool: `set_model` (user-only, not agent-callable)
- `src/decafclaw/tools/delegate.py` — Sub-agent delegation: `delegate_task` forks a child agent for a single subtask (call multiple times for parallel work)
- `src/decafclaw/tools/tool_registry.py` — Tool classification (always-loaded vs deferred), token estimation, deferred list formatting
- `src/decafclaw/tools/search_tools.py` — `tool_search` tool: keyword and exact-name lookup for deferred tools
- `src/decafclaw/reflection.py` — Self-reflection: judge call, prompt assembly, result parsing (Reflexion pattern)
- `src/decafclaw/commands.py` — User-invokable commands: trigger parsing, argument substitution, execution (fork/inline)
- `src/decafclaw/tools/confirmation.py` — Shared confirmation request helper (bridges to ConversationManager)
- `src/decafclaw/runner.py` — Top-level orchestrator: manages MCP, HTTP server, Mattermost, heartbeat as parallel tasks
- `src/decafclaw/web/` — Web gateway: auth, conversations, conversation folders, WebSocket chat handler
- `src/decafclaw/web/conversation_folders.py` — Per-user conversation folder index (JSON file, metadata-only)
- `src/decafclaw/web/static/` — Frontend: Lit web components, service layer (AuthClient, WebSocketClient, ConversationStore, MessageStore, ToolStatusStore, markdown, utils)
- `src/decafclaw/web/static/components/context-inspector.js` — Context inspection popover: waffle chart, source breakdown, memory candidates

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
make reindex      # Rebuild embedding index from vault files
make migrate-vault # Migrate wiki/memories to unified vault structure
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

Session docs live in `docs/dev-sessions/YYYY-MM-DD-HHMM-slug/` with `spec.md`, `plan.md`, and `notes.md`.

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
- **`ToolResult.data` for structured results.** Tools can return `ToolResult(text=..., data={...})` to provide machine-readable structured data alongside the human-readable text. The agent loop auto-appends a fenced JSON block to the tool result content when `data` is set. Use for tools where the caller needs to branch programmatically on the result.
- **Use `asyncio.Lock` for concurrency guards.** Prefer `asyncio.Lock` over boolean flags — locks auto-release on exception, preventing stuck state.
- **Conversation state in `ConversationState` dataclass.** Per-conversation state (history, skill state, busy flag, etc.) is tracked via `ConversationState` in mattermost.py, not parallel dicts.
- **Tools receive `ctx` as first param.** All tool functions take a runtime context, even if they don't use it yet.
- **Sync vs async tools.** `execute_tool` auto-detects via `asyncio.iscoroutinefunction`. Sync tools run in `asyncio.to_thread`.
- **Tool calls run concurrently.** When the model emits multiple tool calls in one response, they execute via `asyncio.gather` with a semaphore (`max_concurrent_tools`, default 5). Each call gets a forked ctx with its own `current_tool_call_id`. All tool events carry `tool_call_id` for UI correlation.
- **Tool deferral.** When tool definitions exceed `tool_context_budget_pct` (default 10%) of `compaction_max_tokens`, non-essential tools are deferred behind `tool_search`. The model sees a name+description list and fetches full schemas on demand. Always-loaded tools configured via `ALWAYS_LOADED_TOOLS` env var. Fetched tools persist for the conversation.
- **`end_turn` on ToolResult.** Tools can return `ToolResult(text="...", end_turn=True)` to mechanically end the agent turn. The loop makes one final no-tools LLM call (forcing text output), then returns. For review gates, use `end_turn=EndTurnConfirm(message=..., on_approve=..., on_deny=...)` — the agent loop shows confirmation buttons; approval continues the loop, denial ends the turn. `EndTurnConfirm` takes priority over `True` in parallel batches.
- **Checklist execution loop.** Always-loaded tools (`checklist_create`, `checklist_step_done`, `checklist_abort`, `checklist_status`) provide a general-purpose step-by-step execution primitive. The agent iterates within a single turn: do step → call step_done → get next → do next. `end_turn=True` is only set when all steps are complete (agent summarizes and stops). Storage is per-conversation markdown checkboxes at `{workspace}/todos/{conv_id}.md`. The project skill's execute phase can delegate to these tools in the future.
- **Dynamic skill tools via `get_tools(ctx)`.** Skills can export `get_tools(ctx) -> (dict, list)` to supply different tools per turn based on state. Called each iteration before `_build_tool_list()`. Falls back to static `TOOLS`/`TOOL_DEFINITIONS` for skills without it. Refreshes via `_refresh_dynamic_tools()` which tracks provider names to remove stale entries.
- **Events for progress.** Tools publish `tool_status` events via `ctx.publish()`. The agent loop publishes `llm_start/end` and `tool_start/end`. Subscribers (Mattermost, terminal) handle display.
- **ConversationManager owns agent loops.** All transports (WebSocket, Mattermost, interactive terminal) delegate turn lifecycle to the ConversationManager. The manager handles context setup, history loading, confirmation persistence, message queuing, and per-conversation event streams. Transports are thin adapters: parse input, format output, manage connections. Heartbeat and scheduled tasks bypass the manager (fire-and-forget, no persistent state).
- **Confirmations are persistent conversation messages.** Confirmation requests and responses are written to the JSONL archive with `role: "confirmation_request"` / `role: "confirmation_response"`. The agent loop suspends mechanically at confirmations and resumes when resolved. Typed action handlers (`ConfirmationAction` enum) determine what happens on approval/denial. Pending confirmations survive page reload and server restart (startup scan recovers them).
- **Transport adapters subscribe to per-conversation event streams.** Instead of subscribing to the global event bus, transports subscribe to a conversation's event stream via `manager.subscribe(conv_id, callback)`. Events include streaming chunks, tool lifecycle, confirmation requests, and turn completion. The manager bridges global event bus events to per-conversation streams.
- **Web UI conversation management is REST-only.** All conversation listing, creation, renaming, archiving, folder management uses REST endpoints. WebSocket is only for real-time chat streaming, conversation selection/history loading, model changes, and turn cancellation. Conversation folders are metadata-only (per-user JSON index file); archive files stay in place.
- **Mattermost concerns stay in `mattermost.py`.** Progress formatting, placeholder management, threading logic — all in `MattermostClient`.
- **Mattermost PATCH API quirks.** Omitting `props` from a PATCH preserves existing props (including attachments). To strip attachments, you must explicitly send `props: {"attachments": []}`. However, sending a PATCH with only `props` and no `message` field clears the message text, showing "(message deleted)". Always include the message text when patching props — fetch it first if needed.
- **Mattermost interactive button gotchas.** Button IDs must not contain underscores — Mattermost silently drops callbacks. The `http_callback_base` config must be reachable from the Mattermost server's network (not just the local machine). If buttons render but clicking does nothing and no callback hits the server, check: (1) `http_callback_base` points to an IP/host the MM server can reach, (2) the MM server's `AllowedUntrustedInternalConnections` includes that host, (3) the local IP hasn't changed (common on laptops with DHCP).
- **Config via defaults → config.json → env vars.** Config is resolved in priority order: dataclass defaults → `data/{agent_id}/config.json` → env vars. Env vars are highest priority. Dataclass defaults in `config.py`, sub-dataclasses in `config_types.py`.
- **Use `dataclasses.replace()` to copy Config.** Never copy fields manually — new fields get silently lost. This caused a real bug with semantic search in the eval runner. For nested sub-dataclasses, use the nested pattern: `dataclasses.replace(config, agent=dataclasses.replace(config.agent, data_home=tmp, id="eval"))`.
- **Check for running bot instances before starting one.** Only one websocket connection per Mattermost bot account. A second instance silently misses events.
- **Test live in Mattermost and the web UI after merging**, not just lint/pytest. Real agent behavior differs from unit tests.
- **Tool descriptions are a control surface.** Wording changes ("MUST", "NEVER", checklists, "prefer X over Y") measurably change LLM behavior. Use the eval loop to validate.
- **Group tools by noun, not verb.** `conversation_search` + `conversation_compact` in one module, not scattered across core.
- **Shell approval via `check_shell_approval()`.** All shell-type tools (shell, background start) use the shared `check_shell_approval()` in `shell_tools.py` for confirmation logic. Don't duplicate the approval checks — add new shell tools as callers of this function.
- **Zero tolerance for warnings and traceback noise.** Warnings, tracebacks, and noisy error output obscure real issues. If you see them — even on shutdown, even if they're "harmless" — fix them. Catch exceptions at the right level, suppress expected cancellation errors, and keep logs clean. Bare `except: pass` is never acceptable — at minimum use `except Exception as exc: log.debug(...)` so failures are diagnosable.
- **Bug fix = test first.** When fixing a bug, first write a test that reproduces it (fails), then fix the code to make it pass. This ensures regressions are caught and documents the bug's trigger condition.
- **No deprecated code for test compatibility.** When replacing a function, rewrite its tests to use the new path immediately. Don't keep dead code around with "will be removed later" — it never gets removed and the stale code misleads future readers.
- **Stdlib imports at module level.** Don't put `import re`, `import base64`, etc. inside function bodies unless there's a circular-dependency reason. Function-level imports are for breaking import cycles only.
- **New runtime state goes on the dataclass.** Don't set undeclared attributes on Config or Context via `setattr`. If you need a new field, add it to the dataclass with a default. Using `getattr(obj, "_private_field", fallback)` to read undeclared attributes is a maintenance trap.
- **Commit after each logical step.** Lint and test before committing.
- **Work in a branch for iterative changes.** When making multiple related fixes (especially to UX-sensitive code like streaming/placeholder logic), work in a branch and test the full set before merging to main. Don't push rapid-fire fixes directly to main — regressions compound.
- **One agent turn per conversation at a time.** Concurrent conversations (different threads/channels) are fine.
- **Vault is the unified knowledge base.** Configurable root (default `workspace/vault/`), with agent files under `agent/`. Agent pages in `agent/pages/`, daily journal in `agent/journal/`. User's Obsidian vault can be the vault root. Config: `vault_path`, `agent_folder` in config.json.
- **Agent data at `data/{agent_id}/`.** Admin files (SOUL.md, AGENT.md, USER.md, COMPACTION.md, config.yaml) live at the root — read-only to the agent. Agent read/write files live in `workspace/` subdirectory.
- **System prompt from files.** SOUL.md + AGENT.md bundled in code, overridable at `data/{agent_id}/`. USER.md is workspace-only.
- **Skills are lazy-loaded (unless always-loaded).** Skill catalog (name + description) is in the system prompt. Full content and tools only load when the agent calls `activate_skill`. Per-conversation activation via `ctx.extra_tools`. Exception: skills with `always-loaded: true` in SKILL.md are auto-activated at startup — body in system prompt, tools always available, exempt from deferral.
- **Pages are curated knowledge, journal is episodic.** Journal entries (`vault_journal_append`) are append-only timestamped observations. Pages (`vault_write`) are living documents revised over time. The dream process distills journal entries into curated pages. Vault is Obsidian-compatible — filenames are page titles, `[[wiki-links]]` work. Embedding source types: `page` (agent), `user` (user's Obsidian), `journal`, `conversation`.
- **User-invokable commands.** Skills with `user-invocable: true` can be triggered by `!name` (Mattermost) or `/name` (web UI). Supports `$ARGUMENTS`/`$0`/`$1` substitution, `context: fork` for isolated execution, and `allowed-tools` for tool pre-approval. `!help`/`/help` lists available commands.
- **Skill permissions at agent level.** `data/{agent_id}/skill_permissions.json` — outside the workspace, so the agent can't grant itself permission. User confirms activation with yes/no/always.
- **Bundled skills in `src/decafclaw/skills/`.** Each skill has SKILL.md (required) + tools.py (optional for native Python tools). Skill scan order: workspace > agent-level > bundled.
- **Skills must use absolute imports.** The skill loader uses `importlib.spec_from_file_location` without package context, so relative imports (`from .` or `from ...`) fail at runtime. Use `from decafclaw.skills.my_skill.module import ...` instead.
- **Skill config via `SkillConfig` dataclass in `tools.py`.** Skills own their config schema by exporting a `SkillConfig` dataclass. The loader resolves it at activation time via `load_sub_config` (env vars + `config.skills[name]` dict + defaults). `init(config, skill_config)` receives both the global config and the typed skill config. Skills without `SkillConfig` get the old `init(config)` signature.
- **Multi-provider LLM support.** Config has two layers: `providers` (connection configs: type, credentials, region) and `model_configs` (named model + provider ref + per-model settings). Provider types: `vertex` (Gemini via ADC), `openai` (direct API), `litellm` (OpenAI-compat proxy/Ollama/vLLM). `default_model` sets the conversation default. Users switch models via web UI dropdown or WebSocket `set_model` message — the agent cannot change its own model (cost control). Model selection persisted in archive as `{"role": "model"}` messages. Legacy `LlmConfig` auto-migrates to a "default" litellm provider.
- **MCP servers are globally available.** Configured in `data/{agent_id}/mcp_servers.json` (Claude Code compatible format). Connected eagerly on startup, tools namespaced as `mcp__<server>__<tool>`. Module-level global registry in `mcp_client.py`.
- **MCP auto-restart.** Crashed stdio servers auto-reconnect on next tool call with exponential backoff (max 3 retries). Use `mcp_status(action="restart")` for manual control.
- **Scheduled tasks via cron-style files.** Markdown files with YAML frontmatter in `data/{agent_id}/schedules/` (admin) and `workspace/schedules/` (agent-writable). Frontmatter fields: `schedule` (5-field cron), `channel` (Mattermost channel **ID** — `#name` resolution not yet implemented), `enabled`, `model`, `allowed-tools`, `required-skills`. Independent timer loop (60s poll), per-task last-run tracking in `workspace/.schedule_last_run/`. Uses `croniter` for cron evaluation. Mattermost channel reporting not yet wired — results currently go to agent log.
- **Skill schedule frontmatter.** Skills can declare `schedule: "cron expression"` in SKILL.md to run as scheduled tasks. Only bundled and admin-level skills are honored (workspace skills cannot self-schedule). File-based schedules override skill schedules on name collision. Skills with both `schedule` and `user-invocable: true` serve as both scheduled tasks and on-demand commands.
- **Pre-compaction memory sweep.** Before compaction summarizes old history, a background child agent reviews the about-to-be-compacted messages and saves noteworthy information to the vault. Runs as an isolated `asyncio.Task` with vault tools only — does not block compaction. Controlled by `compaction.memory_sweep_enabled` (default true). Sweep prompt loaded from `data/{agent_id}/MEMORY_SWEEP.md` with bundled fallback. Fail-open: errors logged and discarded.
- **Self-reflection is fail-open.** The reflection judge evaluates responses before delivery, but errors (network, parse, etc.) always pass through the response as-is. Retries consume `max_tool_iterations` budget. Skipped for child agents, cancelled turns, and empty responses.
- **Context assembly via ContextComposer.** All context for an LLM turn is assembled by `ContextComposer.compose()` in `context_composer.py`. This produces a `ComposedContext` with messages, tools, deferred tools, token estimates, and per-source diagnostics. The composer is stateful per-conversation (via `ComposerState` on `ctx.composer`), tracking what was included and actual token usage across turns. Mode-aware: `INTERACTIVE`, `HEARTBEAT`, `SCHEDULED`, `CHILD_AGENT` control which sources are included. Tool assembly in the iteration loop still uses `_build_tool_list()` since fetched tools change mid-turn.
- **Relevance scoring for memory context.** Retrieval candidates are scored by `composite_score = w_similarity * similarity + w_recency * recency + w_importance * importance`. Weights configurable via `RelevanceConfig`. Candidates ranked by score; dynamic budget allocation fills from top until budget exhausted. Fixed costs (system prompt, history, tools, explicit `@[[Page]]` refs) reserved first.
- **Vault page frontmatter.** Vault pages support optional YAML frontmatter with `summary`, `keywords`, `tags`, `importance` fields. Parsed by `frontmatter.py`. Composite embeddings prepend metadata to body for richer semantic search. Frontmatter is LLM-generated (Phase B) and human-editable.
- **Wiki-link graph expansion.** Memory context retrieval follows `[[wiki-links]]` one hop from top embedding hits to expand the candidate pool. Linked pages get discounted similarity and compete on composite score. Configurable via `RelevanceConfig.graph_expansion_enabled`.
- **Proactive memory context is fail-open.** Before each interactive turn, relevant memories/wiki are auto-injected as context via the ContextComposer. Errors silently return empty results. Skipped for heartbeat, scheduled tasks, and child agents. Requires an embedding model to be configured — silently disabled otherwise.
- **Vault chat context.** Users can share vault pages into conversations via `@[[PageName]]` mentions (all channels) or by having a page open in the web UI sidebar. Pages are injected once per conversation as `vault_references` role messages, tracked by scanning history. Parsing happens in the ContextComposer via helpers in `agent.py`. Page resolution uses vault root, not a fixed wiki directory.
- **Context diagnostics sidecar.** After each turn, the agent loop writes `workspace/conversations/{conv_id}.context.json` with per-source token estimates, scoring details, and memory candidate breakdowns. REST endpoint `GET /api/conversations/{id}/context` returns this data. Web UI popover with waffle chart visualization triggered by clicking the context bar.
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
