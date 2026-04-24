# DecafClaw — Development Conventions

## What is this?

An AI agent testbed for exploring agent development patterns. Connects to Mattermost as a chat bot, with a web UI and interactive terminal mode as alternatives. Multi-provider LLM support (Vertex/Gemini, OpenAI, LiteLLM-compat) with named model configs. Tabstack for web tools.

## Architecture

- **Event-driven**: EventBus pub/sub (`events.py`) decouples tool execution from message delivery
- **Runtime context**: Go-inspired forkable Context (`context.py`) carries config + event bus, forked per-request
- **Async throughout**: Agent loop, LLM client, and tool execution are all async. Streaming Tabstack tools use `AsyncTabstack`.
- **Per-conversation state**: Threads and top-level channel messages are independent conversations, keyed by `root_id` (threads) or `channel_id` (top-level)

## Conventions

### Code style

- **Prefer clarity over abstraction.** This is an exploration project — we try to keep things simple but aren't afraid to explore complexity when a feature calls for it. Readability matters more than cleverness.
- **Files on disk, human-readable.** All agent state uses files you can read, edit, and inspect: markdown for memories and to-dos, JSONL for conversation archives, SQLite for embeddings. No opaque databases. Crash-recoverable by design.
- **Stdlib imports at module level.** Don't put `import re`, `import base64`, etc. inside function bodies unless there's a circular-dependency reason. Function-level imports are for breaking import cycles only.
- **New runtime state goes on the dataclass.** Don't set undeclared attributes on Config or Context via `setattr`. If you need a new field, add it to the dataclass with a default. Using `getattr(obj, "_private_field", fallback)` to read undeclared attributes is a maintenance trap.
- **Use `dataclasses.replace()` to copy Config.** Never copy fields manually — new fields get silently lost. This caused a real bug with semantic search in the eval runner. For nested sub-dataclasses, use the nested pattern: `dataclasses.replace(config, agent=dataclasses.replace(config.agent, data_home=tmp, id="eval"))`.
- **Use `asyncio.Lock` for concurrency guards.** Prefer `asyncio.Lock` over boolean flags — locks auto-release on exception, preventing stuck state.
- **Zero tolerance for warnings and traceback noise.** Warnings, tracebacks, and noisy error output obscure real issues. If you see them — even on shutdown, even if they're "harmless" — fix them. Catch exceptions at the right level, suppress expected cancellation errors, and keep logs clean. Bare `except: pass` is never acceptable — at minimum use `except Exception as exc: log.debug(...)` so failures are diagnosable.
- **No deprecated code for test compatibility.** When replacing a function, rewrite its tests to use the new path immediately. Don't keep dead code around with "will be removed later" — it never gets removed and the stale code misleads future readers.

### Tools

- **Tools receive `ctx` as first param.** All tool functions take a runtime context, even if they don't use it yet.
- **Sync vs async tools.** `execute_tool` auto-detects via `asyncio.iscoroutinefunction`. Sync tools run in `asyncio.to_thread`.
- **Tool calls run concurrently.** When the model emits multiple tool calls in one response, they execute via `asyncio.gather` with a semaphore (`max_concurrent_tools`, default 5). Each call gets a forked ctx with its own `current_tool_call_id`. All tool events carry `tool_call_id` for UI correlation.
- **Tool error returns use `ToolResult`.** Error returns should use `ToolResult(text="[error: ...]")` rather than bare strings, for consistency across all tool modules.
- **`ToolResult.data` for structured results.** Tools can return `ToolResult(text=..., data={...})` to provide machine-readable structured data alongside the human-readable text. The agent loop auto-appends a fenced JSON block to the tool result content when `data` is set.
- **Group tools by noun, not verb.** `conversation_search` + `conversation_compact` in one module, not scattered across core.
- **Tool descriptions are a control surface.** Wording changes ("MUST", "NEVER", checklists, "prefer X over Y") measurably change LLM behavior. Use the eval loop to validate.
- **Shell approval via `check_shell_approval()`.** All shell-type tools (shell, background start) use the shared `check_shell_approval()` in `shell_tools.py` for confirmation logic. Don't duplicate the approval checks — add new shell tools as callers of this function.
- **Tool priority & deferral.** Every core tool declares a `priority` field in `TOOL_DEFINITIONS`: `critical` (always active), `normal` (default; fills remaining budget), `low` (fetched on demand). Activated skill tools and fetched tools are treated as critical. When active tool tokens exceed `tool_context_budget_pct` × `compaction_max_tokens` or count exceeds `max_active_tools`, the classifier fills the active set tier by tier. Non-active tools are deferred behind `tool_search` — the model sees a name+description catalog and fetches full schemas on demand. User override via `CRITICAL_TOOLS` env var force-promotes named tools to critical.
- **Per-tool timeout.** Every non-MCP tool call is wrapped with a wall-clock timer in `execute_tool`. Default `agent.tool_timeout_sec` is 180s (env `TOOL_TIMEOUT_SEC`). Per-tool overrides live on `TOOL_DEFINITIONS` entries as a `timeout` key alongside `priority`: a positive int sets a custom bound, `None` opts out entirely. Current opt-outs: `delegate_task` (own child-agent timeout), `conversation_compact` (LLM summarization bound by model timeout), `claude_code_send` (multi-minute subprocess sessions). MCP tools are untouched — their per-server timeout in `mcp_client.py` remains authoritative. Setting the global config `<= 0` disables the wrapper entirely.
- **Pre-emptive tool search.** At turn start, `ContextComposer._compose_preempt_matches` tokenizes the user message + most recent assistant response and keyword-matches against tool names/descriptions. Matches land on `ctx.tools.preempt_matches` and are passed to `classify_tools` as a fourth "force critical" source alongside env override / fetched / skill tools. Promotions are ephemeral (recomputed each turn; calling a matched tool does NOT auto-fetch it). Configure via `config.agent.preemptive_search.enabled` and `.max_matches`.
- **Checklist execution loop.** Always-loaded tools (`checklist_create`, `checklist_step_done`, `checklist_abort`, `checklist_status`) provide a general-purpose step-by-step execution primitive. The agent iterates within a single turn: do step → call step_done → get next → do next. `end_turn=True` is only set when all steps are complete. Storage is per-conversation markdown checkboxes at `{workspace}/todos/{conv_id}.md`.
- **`end_turn` on ToolResult.** Tools can return `ToolResult(text="...", end_turn=True)` to mechanically end the agent turn. The loop makes one final no-tools LLM call (forcing text output), then returns. For review gates, use `end_turn=EndTurnConfirm(message=..., on_approve=..., on_deny=...)` — the agent loop shows confirmation buttons; approval continues the loop, denial ends the turn. `EndTurnConfirm` takes priority over `True` in parallel batches.
- **Events for progress.** Tools publish `tool_status` events via `ctx.publish()`. The agent loop publishes `llm_start/end` and `tool_start/end`. Subscribers (Mattermost, terminal) handle display.

### Skills

- **Skills are lazy-loaded (unless always-loaded).** Skill catalog (name + description) is in the system prompt. Full content and tools only load when the agent calls `activate_skill`. Per-conversation activation via `ctx.extra_tools`. Exception: skills with `always-loaded: true` in SKILL.md are auto-activated at startup — body in system prompt, tools always available, exempt from deferral.
- **Dynamic skill tools via `get_tools(ctx)`.** Skills can export `get_tools(ctx) -> (dict, list)` to supply different tools per turn based on state. Called each iteration before `_build_tool_list()`. Falls back to static `TOOLS`/`TOOL_DEFINITIONS` for skills without it. Refreshes via `_refresh_dynamic_tools()` which tracks provider names to remove stale entries.
- **Bundled skills in `src/decafclaw/skills/`.** Each skill has SKILL.md (required) + tools.py (optional for native Python tools). Skill scan order: workspace > agent-level > bundled.
- **Skills must use absolute imports.** The skill loader uses `importlib.spec_from_file_location` without package context, so relative imports (`from .` or `from ...`) fail at runtime. Use `from decafclaw.skills.my_skill.module import ...` instead.
- **Skill config via `SkillConfig` dataclass in `tools.py`.** Skills own their config schema by exporting a `SkillConfig` dataclass. The loader resolves it at activation time via `load_sub_config` (env vars + `config.skills[name]` dict + defaults). `init(config, skill_config)` receives both the global config and the typed skill config. Skills without `SkillConfig` get the old `init(config)` signature.
- **Skill permissions at agent level.** `data/{agent_id}/skill_permissions.json` — outside the workspace, so the agent can't grant itself permission. User confirms activation with yes/no/always.
- **User-invokable commands.** Skills with `user-invocable: true` can be triggered by `!name` (Mattermost) or `/name` (web UI). Supports `$ARGUMENTS`/`$0`/`$1` substitution, `context: fork` for isolated execution, and `allowed-tools` for tool pre-approval. `!help`/`/help` lists available commands.
- **Skill schedule frontmatter.** Skills can declare `schedule: "cron expression"` in SKILL.md to run as scheduled tasks. Only bundled and admin-level skills are honored (workspace skills cannot self-schedule). File-based schedules override skill schedules on name collision.

### Config and data

- **Config via defaults → config.json → env vars.** Config is resolved in priority order: dataclass defaults → `data/{agent_id}/config.json` → env vars. Env vars are highest priority. Dataclass defaults in `config.py`, sub-dataclasses in `config_types.py`.
- **Agent data at `data/{agent_id}/`.** Admin files (SOUL.md, AGENT.md, USER.md, COMPACTION.md, config.json) live at the root — read-only to the agent. Agent read/write files live in `workspace/` subdirectory.
- **System prompt from files.** SOUL.md + AGENT.md bundled in code, overridable at `data/{agent_id}/`. USER.md is workspace-only.
- **Vault is the unified knowledge base.** Configurable root (default `workspace/vault/`), with agent files under `agent/`. Agent pages in `agent/pages/`, daily journal in `agent/journal/`. User's Obsidian vault can be the vault root. Config: `vault_path`, `agent_folder` in config.json.
- **Pages are curated knowledge, journal is episodic.** Journal entries (`vault_journal_append`) are append-only timestamped observations. Pages (`vault_write`) are living documents revised over time. The dream process distills journal entries into curated pages. Vault is Obsidian-compatible — filenames are page titles, `[[wiki-links]]` work. Embedding source types: `page` (agent), `user` (user's Obsidian), `journal`, `conversation`.
- **Multi-provider LLM support.** Config has two layers: `providers` (connection configs: type, credentials, region) and `model_configs` (named model + provider ref + per-model settings). Provider types: `vertex` (Gemini via ADC), `openai` (direct API), `litellm` (OpenAI-compat proxy/Ollama/vLLM). `default_model` sets the conversation default. Users switch models via web UI dropdown or WebSocket `set_model` message — the agent cannot change its own model (cost control). Model selection persisted in archive as `{"role": "model"}` messages. Legacy `LlmConfig` auto-migrates to a "default" litellm provider.
- **MCP servers are globally available.** Configured in `data/{agent_id}/mcp_servers.json` (Claude Code compatible format). Connected eagerly on startup, tools namespaced as `mcp__<server>__<tool>`. Module-level global registry in `mcp_client.py`.
- **MCP auto-restart.** Crashed stdio servers auto-reconnect on next tool call with exponential backoff (max 3 retries). Use `mcp_status(action="restart")` for manual control.
- **Scheduled tasks via cron-style files.** Markdown files with YAML frontmatter in `data/{agent_id}/schedules/` (admin) and `workspace/schedules/` (agent-writable). Frontmatter fields: `schedule` (5-field cron), `channel` (Mattermost channel **ID**), `enabled`, `model`, `allowed-tools`, `required-skills`. Independent timer loop (60s poll), per-task last-run tracking in `workspace/.schedule_last_run/`. Uses `croniter` for cron evaluation.
- **Notification inbox for agent-initiated events.** Heartbeat, scheduled-task, and background-job events append JSONL records under `workspace/notifications/` via `notify()` / `ctx.notify()`. Stored append-only with opportunistic time-based rotation; read-state reconstructed from a companion `read.jsonl`. The web UI bell is **push-driven over the authenticated WebSocket** (no polling): `notification_created` and `notification_read` events flow from the bus to every connected socket, with an `unread_count` computed once at publish time. `GET /api/notifications/unread-count` is kept as a seed on component mount + every WebSocket reconnect. All producers are fail-open — errors logged, never raised. See [docs/notifications.md](docs/notifications.md). For the agent-facing complement (wake turns on job completion), see [docs/background-wake.md](docs/background-wake.md).
- **Notification channel adapters are EventBus subscribers.** After the inbox append, `notify()` publishes a `notification_created` event (payload: `record` + `unread_count`). Channel adapters (Mattermost DM, email, vault page; Mattermost channel / etc. later) live in `src/decafclaw/notification_channels/`. A single `init_notification_channels(config, event_bus, **deps)` in that package's `__init__.py` handles all startup wiring — each channel's enable-guards + `event_bus.subscribe` call lives there, so adding a new channel touches its own module + `notification_channels/__init__.py` only (not `runner.py`). Adapters filter per-event against their own config and fire-and-forget delivery via `asyncio.create_task` so `notify()` never blocks. Inbox stays authoritative; channels are best-effort. The vault page channel writes a daily rollup file at `<vault_root>/agent/pages/notifications/YYYY-MM-DD.md` (folder configurable); it deliberately skips the embedding index — notifications are a rolling log, not reference material. The web WebSocket handler (`src/decafclaw/web/websocket.py`) is itself a bus subscriber that forwards notification events to its socket via `_make_notification_forwarder(ws_send)`, unsubscribing on disconnect.
- **Email is dual-surface: agent tool + notification channel.** `src/decafclaw/mail.py` is the shared async SMTP core (aiosmtplib, STARTTLS + plain AUTH). The `send_email` tool gates every call via `check_email_approval` (allowlist bypass, otherwise `request_confirmation`); the allowlist is a union of `config.email.allowed_recipients` and per-scheduled-task `email-recipients` frontmatter (threaded through `ctx.tools.preapproved_email_recipients`, mirroring the `shell_patterns` pattern). Attachments resolve under `config.workspace_path` with a summed size cap. The email notification channel bypasses the tool's allowlist — its own `recipient_addresses` config IS the trust boundary. See [docs/email.md](docs/email.md).
- **LOG_LEVEL env var.** Set `LOG_LEVEL=DEBUG` for verbose logging (default: INFO).

### Context assembly

- **Context assembly via ContextComposer.** All context for an LLM turn is assembled by `ContextComposer.compose()` in `context_composer.py`. This produces a `ComposedContext` with messages, tools, deferred tools, token estimates, and per-source diagnostics. The composer is stateful per-conversation (via `ComposerState` on `ctx.composer`), tracking what was included and actual token usage across turns. Mode-aware: `INTERACTIVE`, `HEARTBEAT`, `SCHEDULED`, `CHILD_AGENT` control which sources are included. Tool assembly in the iteration loop still uses `_build_tool_list()` since fetched tools change mid-turn.
- **Relevance scoring for memory context.** Retrieval candidates are scored by `composite_score = w_similarity * similarity + w_recency * recency + w_importance * importance`. Weights configurable via `RelevanceConfig`. Candidates ranked by score; dynamic budget allocation fills from top until budget exhausted. Fixed costs (system prompt, history, tools, explicit `@[[Page]]` refs) reserved first.
- **Vault page frontmatter.** Vault pages support optional YAML frontmatter with `summary`, `keywords`, `tags`, `importance` fields. Parsed by `frontmatter.py`. Composite embeddings prepend metadata to body for richer semantic search. Frontmatter is LLM-generated and human-editable.
- **Wiki-link graph expansion.** Memory context retrieval follows `[[wiki-links]]` one hop from top embedding hits to expand the candidate pool. Linked pages get discounted similarity and compete on composite score. Configurable via `RelevanceConfig.graph_expansion_enabled`.
- **Proactive memory context is fail-open.** Before each interactive turn, relevant memories/wiki are auto-injected as context via the ContextComposer. Errors silently return empty results. Skipped for heartbeat, scheduled tasks, and child agents. Requires an embedding model to be configured — silently disabled otherwise.
- **Vault chat context.** Users can share vault pages into conversations via `@[[PageName]]` mentions (all channels) or by having a page open in the web UI sidebar. Pages are injected once per conversation as `vault_references` role messages, tracked by scanning history. Parsing happens in the ContextComposer via helpers in `agent.py`. Page resolution uses vault root, not a fixed wiki directory.
- **Context diagnostics sidecar.** After each turn, the agent loop writes `workspace/conversations/{conv_id}.context.json` with per-source token estimates, scoring details, and memory candidate breakdowns. REST endpoint `GET /api/conversations/{id}/context` returns this data. Web UI popover with waffle chart visualization triggered by clicking the context bar.

### Agent behavior

- **One agent turn per conversation at a time.** Concurrent conversations (different threads/channels) are fine.
- **ConversationManager owns agent loops.** All transports (WebSocket, Mattermost, interactive terminal) delegate turn lifecycle to the ConversationManager. The manager handles context setup, history loading, confirmation persistence, message queuing, and per-conversation event streams. Transports are thin adapters: parse input, format output, manage connections. **All turn orchestration — user messages, heartbeat sections, scheduled tasks, child-agent delegations, and background-job wake turns — routes through the ConversationManager via `enqueue_turn(kind=...)`. Each conversation has a single busy flag that serializes all turn kinds, so wakes can fire safely alongside in-flight user turns. The `TurnKind` enum (USER, HEARTBEAT_SECTION, SCHEDULED_TASK, CHILD_AGENT, WAKE) drives per-kind policy for context construction, circuit-breaker handling, and state persistence.**
- **Confirmations are persistent conversation messages.** Confirmation requests and responses are written to the JSONL archive with `role: "confirmation_request"` / `role: "confirmation_response"`. The agent loop suspends mechanically at confirmations and resumes when resolved. Typed action handlers (`ConfirmationAction` enum) determine what happens on approval/denial. Pending confirmations survive page reload and server restart (startup scan recovers them).
- **Transport adapters subscribe to per-conversation event streams.** Instead of subscribing to the global event bus, transports subscribe to a conversation's event stream via `manager.subscribe(conv_id, callback)`. Events include streaming chunks, tool lifecycle, confirmation requests, and turn completion. The manager bridges global event bus events to per-conversation streams.
- **Self-reflection is fail-open.** The reflection judge evaluates responses before delivery, but errors (network, parse, etc.) always pass through the response as-is. Retries consume `max_tool_iterations` budget. Skipped for child agents, cancelled turns, and empty responses.
- **Pre-compaction memory sweep.** Before compaction summarizes old history, a background child agent reviews the about-to-be-compacted messages and saves noteworthy information to the vault. Runs as an isolated `asyncio.Task` with vault tools only — does not block compaction. Controlled by `compaction.memory_sweep_enabled` (default true). Sweep prompt loaded from `data/{agent_id}/MEMORY_SWEEP.md` with bundled fallback. Fail-open: errors logged and discarded.

### Mattermost-specific

- **Mattermost concerns stay in `mattermost.py`.** Progress formatting, placeholder management, threading logic — all in `MattermostClient`.
- **Web UI conversation management is REST-only.** All conversation listing, creation, renaming, archiving, folder management uses REST endpoints. WebSocket is only for real-time chat streaming, conversation selection/history loading, model changes, and turn cancellation. Conversation folders are metadata-only (per-user JSON index file); archive files stay in place. Workspace file management for the Files tab is also REST-only via `/api/workspace/*` (browse, recent, read-json, write, delete, create, rename) — see [docs/files-tab.md](docs/files-tab.md).
- **Mattermost PATCH API quirks.** Omitting `props` from a PATCH preserves existing props (including attachments). To strip attachments, you must explicitly send `props: {"attachments": []}`. However, sending a PATCH with only `props` and no `message` field clears the message text, showing "(message deleted)". Always include the message text when patching props — fetch it first if needed.
- **Mattermost interactive button gotchas.** Button IDs must not contain underscores — Mattermost silently drops callbacks. The `http_callback_base` config must be reachable from the Mattermost server's network (not just the local machine). If buttons render but clicking does nothing and no callback hits the server, check: (1) `http_callback_base` points to an IP/host the MM server can reach, (2) the MM server's `AllowedUntrustedInternalConnections` includes that host, (3) the local IP hasn't changed (common on laptops with DHCP).
- **Check for running bot instances before starting one.** Only one websocket connection per Mattermost bot account. A second instance silently misses events.

### Workflow

- **Sync with origin/main before starting any significant task.** Run `git fetch origin && git log --oneline main..origin/main` at session start — before branching, and especially before long audit/research work. Auditing against stale local main produces output that contradicts current reality: "ghost" references to features that already exist, missing new docs, wrong convention wording. A reusable workflow bug: every session where this was skipped ended with conflicts or redone work.
- **Bug fix = test first.** When fixing a bug, first write a test that reproduces it (fails), then fix the code to make it pass. This ensures regressions are caught and documents the bug's trigger condition.
- **Commit after each logical step.** Lint and test before committing.
- **Work in a branch for iterative changes.** When making multiple related fixes (especially to UX-sensitive code like streaming/placeholder logic), work in a branch and test the full set before merging to main. Don't push rapid-fire fixes directly to main — regressions compound.
- **Test live in Mattermost and the web UI after merging**, not just lint/pytest. Real agent behavior differs from unit tests.
- **Test speed discipline.** Tests run in parallel by default (`pytest-xdist -n auto` via `pyproject.toml`), so slow tests block workers for their full duration. Two patterns to avoid:
  - **Don't `asyncio.sleep(X)` to wait for work to finish.** Wait on the right signal instead: `await job.reader_task` for a subprocess completion, `asyncio.wait_for(event.wait(), timeout=...)` for a flag, or patch the underlying clock (`monkeypatch` `_now_iso` / `time.monotonic`) when you need timestamp ordering. A fixed sleep is both slower *and* flakier than waiting on the actual completion event.
  - **Anything that runs a real scheduler or timer loop must patch the work function.** `discover_schedules` picks up bundled scheduled skills (`dream`, `garden`) from disk; on a fresh `tmp_path` config they're treated as "never run → due" and fire a real `run_agent_turn`. If your test calls `run_schedule_timer` without patching `run_schedule_task`, the `finally`-block drain will wait for those simulated agent turns to complete (seen: one test taking ~66s). Patch `decafclaw.schedules.run_schedule_task` with a trivial `fake_run` coroutine even for "no tasks" scenarios.
- **Check `pytest --durations=25` when adding tests.** If a new test lands in the top 25, figure out why before committing — it's usually a missing mock or a fixed sleep masquerading as a synchronization primitive.

## Key files

### Core

- `src/decafclaw/__init__.py` — Entry point, config/context setup, mode dispatch
- `src/decafclaw/agent.py` — Agent loop: turn orchestration, tool execution, LLM calls
- `src/decafclaw/conversation_manager.py` — Central orchestrator: agent loop lifecycle, confirmation persistence, per-conversation event streams, TurnKind dispatch, wake turn scheduling
- `src/decafclaw/confirmations.py` — Confirmation types (ConfirmationAction, Request, Response), handler registry
- `src/decafclaw/context.py` — Forkable runtime context with sub-objects: TokenUsage, ToolState, SkillState, ComposerState
- `src/decafclaw/context_composer.py` — Context composer: unified context assembly, relevance scoring, dynamic budget allocation
- `src/decafclaw/events.py` — In-process pub/sub event bus
- `src/decafclaw/runner.py` — Top-level orchestrator: manages MCP, HTTP server, Mattermost, heartbeat as parallel tasks

### Config

- `src/decafclaw/config.py` — Dataclass config from env vars / .env
- `src/decafclaw/config_types.py` — Config sub-dataclasses (ProviderConfig, ModelConfig, LlmConfig, MattermostConfig, etc.)
- `src/decafclaw/config_cli.py` — CLI tool for config show/get/set

### LLM

- `src/decafclaw/llm/` — LLM client package: provider abstraction, registry, multi-provider support
- `src/decafclaw/llm/types.py` — Provider protocol, StreamCallback type
- `src/decafclaw/llm/registry.py` — Provider registry: init, lookup, lifecycle
- `src/decafclaw/llm/providers/openai_compat.py` — OpenAI-compatible provider (httpx + SSE): LiteLLM, Ollama, vLLM, OpenRouter
- `src/decafclaw/llm/providers/openai.py` — Direct OpenAI API provider
- `src/decafclaw/llm/providers/vertex.py` — Vertex AI Gemini provider (native REST + ADC auth)

### Transports

- `src/decafclaw/interactive_terminal.py` — Interactive terminal mode (stdin/stdout REPL)
- `src/decafclaw/mattermost.py` — Mattermost transport: message handling, flood protection, progress subscriber
- `src/decafclaw/mattermost_display.py` — ConversationDisplay: per-turn Mattermost message sequencing
- `src/decafclaw/mattermost_ui.py` — Mattermost UI helpers: confirmation buttons, stop buttons, token registry
- `src/decafclaw/http_server.py` — HTTP server (Starlette/uvicorn): interactive button callbacks, health check
- `src/decafclaw/web/` — Web gateway: auth, conversations, WebSocket chat handler
- `src/decafclaw/web/auth.py` — Token-based authentication for the web gateway
- `src/decafclaw/web/conversations.py` — Conversation index: lightweight metadata for web UI
- `src/decafclaw/web/conversation_folders.py` — Per-user conversation folder index (JSON file, metadata-only)
- `src/decafclaw/web/websocket.py` — WebSocket handler for web gateway chat
- `src/decafclaw/web/workspace_paths.py` — Permission helpers + kind detection for the `/api/workspace/*` endpoints (secret/readonly patterns, text/image/binary sniff)
- `src/decafclaw/web/static/` — Frontend: Lit web components, service layer
- `src/decafclaw/web/static/components/context-inspector.js` — Context inspection popover: waffle chart, source breakdown
- `src/decafclaw/web/static/components/vault-sidebar.js` — Vault tab (browse/recent/hidden toggle), extracted from the old inline sidebar code
- `src/decafclaw/web/static/components/files-sidebar.js` — Files tab: workspace browser (browse/recent), auto-refetch on turn-complete
- `src/decafclaw/web/static/components/file-page.js` — Workspace file content pane: text/image/binary modes, rename, delete, conflict recovery
- `src/decafclaw/web/static/components/file-editor.js` — CodeMirror 6 editor for workspace text files with debounced auto-save

### Data and persistence

- `src/decafclaw/archive.py` — Conversation archive (JSONL per conversation)
- `src/decafclaw/compaction.py` — History compaction via summarization, pre-compaction memory sweep
- `src/decafclaw/persistence.py` — Per-conversation state persistence: skills, skill data, sidecars
- `src/decafclaw/attachments.py` — Attachment storage: save, read, list, delete conversation file attachments
- `src/decafclaw/embeddings.py` — Semantic search index (sqlite-vec cosine similarity)
- `src/decafclaw/frontmatter.py` — YAML frontmatter parsing/serialization for vault pages
- `src/decafclaw/memory_context.py` — Vault retrieval: embedding search, graph expansion, metadata enrichment
- `src/decafclaw/checklist.py` — Per-conversation checklist execution loop (markdown checkboxes at `workspace/todos/`)

### Tools

- `src/decafclaw/tools/` — Tool registry and all tool modules
- `src/decafclaw/tools/tool_registry.py` — Priority-based tool classification (critical/normal/low), token estimation, deferred catalog rendering
- `src/decafclaw/tools/search_tools.py` — `tool_search` tool: keyword and exact-name lookup for deferred tools
- `src/decafclaw/preempt_search.py` — Keyword-match library for pre-emptive tool promotion
- `src/decafclaw/tools/core.py` — Core tools (web_fetch, current_time, debug, context_stats)
- `src/decafclaw/tools/workspace_tools.py` — Sandboxed file operations (read, write, edit, search, glob, etc.)
- `src/decafclaw/tools/conversation_tools.py` — Conversation search and compaction tools
- `src/decafclaw/tools/checklist_tools.py` — Checklist tools: create, step_done, abort, status (always-loaded)
- `src/decafclaw/tools/shell_tools.py` — Shell command execution with confirmation logic
- `src/decafclaw/tools/http_tools.py` — General-purpose HTTP request tool: all methods, headers, body, URL-based allowlist
- `src/decafclaw/tools/skill_tools.py` — Skill activation and refresh tools
- `src/decafclaw/tools/delegate.py` — Sub-agent delegation: `delegate_task` forks a child agent for subtasks
- `src/decafclaw/tools/model_tools.py` — Model selection tool: `set_model` (user-only, not agent-callable)
- `src/decafclaw/tools/confirmation.py` — Shared confirmation request helper (bridges to ConversationManager)
- `src/decafclaw/tools/health.py` — Health/diagnostic status tool
- `src/decafclaw/tools/attachment_tools.py` — File attachment tools
- `src/decafclaw/tools/email_tools.py` — `send_email` tool: confirmation-gated outbound email with allowlist bypass + workspace-sandboxed attachments
- `src/decafclaw/tools/heartbeat_tools.py` — Heartbeat trigger tool

### Skills

- `src/decafclaw/skills/` — Skills system: discovery, parsing, catalog, bundled skills
- `src/decafclaw/skills/vault/` — Bundled vault skill: unified knowledge base (pages + journal), always-loaded. Includes `vault_show_sections`, `vault_move_lines`, `vault_section` for section-aware markdown editing.
- `src/decafclaw/skills/tabstack/` — Bundled Tabstack skill (SKILL.md + tools.py)
- `src/decafclaw/skills/dream/` — Dream consolidation: periodic journal review → vault page updates
- `src/decafclaw/skills/garden/` — Vault gardening: structural maintenance sweep (weekly scheduled)
- `src/decafclaw/skills/project/` — Project workflow skill: state machine, plan parser, lifecycle tools
- `src/decafclaw/skills/claude_code/` — Claude Code subagent skill (sessions, permissions, output logging)
- `src/decafclaw/skills/health/` — Bundled `!health` command: agent diagnostic status
- `src/decafclaw/skills/postmortem/` — Bundled `!postmortem` command: blameless RCA on the current conversation, archives report to vault
- `src/decafclaw/skills/ingest/` — Bundled `!ingest` command: one-shot ingest of URL/workspace-file/attachment into vault pages; interactive counterpart to contrib scheduled ingest skills
- `src/decafclaw/skills/background/` — Background process management skill. Bundled, auto-activates.
- `src/decafclaw/skills/mcp/` — MCP admin skill (status, resources, prompts, restart). Bundled, auto-activates.

### Other

- `src/decafclaw/prompts/` — System prompt assembly (SOUL.md + AGENT.md + skill catalog + loader)
- `src/decafclaw/commands.py` — User-invokable commands: trigger parsing, argument substitution, execution
- `src/decafclaw/reflection.py` — Self-reflection: judge call, prompt assembly, result parsing (Reflexion pattern)
- `src/decafclaw/heartbeat.py` — Heartbeat: periodic wake-up, section parsing, timer, cycle runner
- `src/decafclaw/schedules.py` — Scheduled tasks: cron-style task files, discovery, execution, timer loop
- `src/decafclaw/notifications.py` — Notification inbox: append-only JSONL log, rotation, read-state reconstruction, `notify()` API
- `src/decafclaw/notification_channels/` — Notification channel adapters (Mattermost DM, email, vault page; Mattermost channel / etc. later). `init_notification_channels` in `__init__.py` subscribes every enabled adapter to the `notification_created` event bus event at startup.
- `src/decafclaw/mail.py` — Shared async SMTP core (aiosmtplib wrapper). Used by the `send_email` tool and the email notification channel.
- `src/decafclaw/polling.py` — Shared polling loop and task preamble builder (used by heartbeat + schedules)
- `src/decafclaw/mcp_client.py` — MCP client: config, registry, server connections, auto-restart
- `src/decafclaw/media.py` — Media handling: ToolResult, MediaSaveResult, MediaHandler interface
- `src/decafclaw/util.py` — Shared utilities (estimate_tokens)
- `src/decafclaw/eval/` — Eval harness (YAML tests, failure reflection)

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

## Keeping docs current

Documentation lives in `docs/` — see `docs/index.md` for the full list. When adding features, new tools, config options, or architectural changes:
- **Update or add a `docs/` page** — each major feature has its own page. If you're adding a new feature, create a new doc and add it to `docs/index.md`. If you're modifying an existing feature, update its doc.
- **Update `CLAUDE.md`** — key files list, conventions
- **Update `README.md`** — keep it concise; link to docs for details
- **Update `docs/context-composer.md`** — if changing system prompt, tool definitions, or context assembly
- Docs should stay in sync with the code. If you change behavior, check if the docs need updating too. Stale docs are worse than no docs.

**Docs are part of the feature, not an afterthought.** When adding a new subsystem or feature, create the `docs/` page as part of the implementation PR — not as a follow-up. Same for CLAUDE.md key files and conventions.

**At the end of every dev session:**
- Review all `docs/` pages for accuracy — features built, config added, files moved.
- Update `CLAUDE.md` key files list if new modules were added.
- Backlog is what's ahead, not a history of what's done. Git history is the record.

## Known gaps

- No hard history size limit (compaction helps but unbounded archive growth)
