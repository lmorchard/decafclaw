# DecafClaw — Development Conventions

AI agent testbed: chat bot (Mattermost), web UI, terminal REPL. Multi-provider LLM (Vertex/Gemini, OpenAI, LiteLLM-compat). Tabstack for web tools. Architecture overview: [docs/architecture.md](docs/architecture.md). Full doc index: [docs/index.md](docs/index.md).

## Architecture

- **Event-driven**: EventBus pub/sub (`events.py`) decouples tool execution from delivery.
- **Forkable runtime context** (`context.py`): Go-inspired, carries config + event bus, forked per-request.
- **Async throughout**: agent loop, LLM client, tool execution.
- **Per-conversation state**: keyed by `root_id` (threads) or `channel_id` (top-level).

## Conventions

### Code style

- **Prefer clarity over abstraction.** Exploration project — readability beats cleverness.
- **Files on disk, human-readable.** Markdown for memories/todos, JSONL for archives, SQLite for embeddings. Crash-recoverable by design.
- **Stdlib imports at module level.** Function-level imports are for breaking import cycles only.
- **New runtime state goes on the dataclass.** Don't `setattr` undeclared attributes on Config/Context. Don't `getattr(obj, "_field", fallback)` to read undeclared attributes — it's a maintenance trap.
- **Never enumerate fields when copying, forking, snapshotting, or serializing.** Hand-maintained field lists rot silently — when someone adds a field, the copy/snapshot drops it on the floor and nothing complains. We've been bitten more than once: Config eval-runner lost search settings, and `Context.fork_for_tool_call` lost `task_mode` (silently disabling scheduled newsletter email for weeks). Use the right idiom for the shape:
  - **Dataclass copies/forks:** `dataclasses.replace(obj, field=…)`. Nested: `dataclasses.replace(config, agent=dataclasses.replace(config.agent, ...))`.
  - **Dataclass JSON round-trip:** `dataclasses.asdict(obj)` + `Cls(**d)`.
  - **Plain-class snapshots/forks (e.g. `Context`):** `copy.copy(obj)` + explicit overrides for sub-objects that need fresh state.
  - **Tests:** iterate `vars(obj)` or `dataclasses.fields(cls)` — never a hand-listed allowlist (it rots in lockstep with the bug it should catch).
- **Use `asyncio.Lock` for concurrency guards.** Not boolean flags — locks auto-release on exception.
- **Zero tolerance for warnings/traceback noise.** Even on shutdown, even if "harmless" — fix them. Bare `except: pass` is never acceptable; use `except Exception as exc: log.debug(...)`.
- **No deprecated code for test compatibility.** Rewrite tests to the new path immediately.

### Tools

See [docs/tools.md](docs/tools.md), [docs/tool-priority.md](docs/tool-priority.md), [docs/tool-search.md](docs/tool-search.md), [docs/preemptive-tool-search.md](docs/preemptive-tool-search.md).

- **Tools receive `ctx` as first param.** Always, even if unused.
- **`execute_tool` auto-detects sync vs async.** Sync tools run via `asyncio.to_thread`.
- **Tool calls run concurrently** via `asyncio.gather` with a semaphore (`max_concurrent_tools`, default 5). Each call gets a forked ctx with its own `current_tool_call_id`.
- **Errors return `ToolResult(text="[error: ...]")`**, not bare strings. `ToolResult.data` for structured results — auto-rendered as a fenced JSON block.
- **Group tools by noun, not verb.** `conversation_search` + `conversation_compact` in one module.
- **Tool descriptions are a control surface.** Wording changes ("MUST", "NEVER", checklists) measurably change LLM behavior. Run `make eval-tools` to validate disambiguation between overlapping tools.
- **Shell approval via shared `check_shell_approval()`** in `shell_tools.py`. Don't duplicate the approval checks.
- **Per-tool timeout** wraps every non-MCP tool call (default 180s, env `TOOL_TIMEOUT_SEC`). Override via `timeout` key in `TOOL_DEFINITIONS`; `None` opts out. Current opt-outs: `delegate_task`, `conversation_compact`, `claude_code_send`. MCP tools use their own per-server timeout.
- **`end_turn=True` on `ToolResult`** mechanically ends the turn (one final no-tools LLM call, then return). For review gates use `end_turn=EndTurnConfirm(message=..., on_approve=..., on_deny=...)`. For input widgets (`accepts_input=true`), pair with `end_turn=True` and the loop auto-promotes to a `WidgetInputPause` that pauses via the confirmation infra and resumes with the user's selection injected as a synthetic user message. Priority in one batch: `WidgetInputPause` > `EndTurnConfirm` > `True`.
- **Checklist tools (`checklist_create/_step_done/_abort/_status`)** are always-loaded. Iteration happens within a single turn: do step → step_done → next.
- **Events for progress.** Tools publish `tool_status` via `ctx.publish()`.

### Skills

See [docs/skills.md](docs/skills.md).

- **Lazy-loaded by default.** Catalog (name + description) in system prompt; full body and tools load on `activate_skill`. `always-loaded: true` opts a skill out (auto-activated, exempt from deferral).
- **Bundled in `src/decafclaw/skills/`**. Each: SKILL.md (required) + `tools.py` (optional). Scan order: workspace > agent-level > bundled.
- **Skills must use absolute imports** (`from decafclaw.skills.X.Y import ...`). The loader uses `importlib.spec_from_file_location` without package context, so relative imports fail at runtime.
- **Skill config via `SkillConfig` dataclass in `tools.py`.** Resolved at activation by `load_sub_config` (env + `config.skills[name]` + defaults). `init(config, skill_config)` receives both.
- **User-invokable commands** (`user-invocable: true`) trigger via `!name` (Mattermost) / `/name` (web UI). Supports `$ARGUMENTS`/`$0`/`$1`, `context: fork`, `allowed-tools`.
- **`schedule:` frontmatter** turns a skill into a scheduled task. Bundled and admin-level only — workspace skills can't self-schedule.
- **Permissions at `data/{agent_id}/skill_permissions.json`** — outside the workspace, so the agent can't grant itself permission.
- **Dynamic per-turn tools:** export `get_tools(ctx) -> (dict, list)` to vary tools by state.

### Config and data

See [docs/config.md](docs/config.md), [docs/data-layout.md](docs/data-layout.md), [docs/providers.md](docs/providers.md), [docs/model-selection.md](docs/model-selection.md).

- **Resolution order:** dataclass defaults → `data/{agent_id}/config.json` → env vars (highest priority). Dataclasses in `config.py` / `config_types.py`.
- **Agent data layout.** Admin files (SOUL.md, AGENT.md, USER.md, COMPACTION.md, config.json) at `data/{agent_id}/` root, read-only to agent. Agent read/write at `workspace/`.
- **System prompt assembly.** SOUL.md + AGENT.md bundled in code, overridable per-agent. Each section wrapped in XML tags (`<soul>`, `<agent_role>`, `<user_context>`, `<skill_catalog>`, `<loaded_skills>`, `<deferred_tools>`) — wrapping is additive in `load_system_prompt`/`build_deferred_list_text`. Source files stay plain markdown. See [docs/context-composer.md](docs/context-composer.md#section-delimiters).
- **Vault** — unified knowledge base ([docs/vault.md](docs/vault.md)). Pages = curated knowledge (`vault_write`); journal = episodic (`vault_journal_append`); dream skill distills journal → pages. Obsidian-compatible.
- **Multi-provider LLM** ([docs/providers.md](docs/providers.md)): two layers — `providers` (connection) + `model_configs` (named model + provider ref). Users switch models via UI; agent can't change its own model (cost control).
- **MCP servers** ([docs/mcp-servers.md](docs/mcp-servers.md)) globally available, configured in `data/{agent_id}/mcp_servers.json`. Tools namespaced `mcp__<server>__<tool>`. Stdio servers auto-restart with backoff.
- **Scheduled tasks** ([docs/schedules.md](docs/schedules.md)) — cron-style markdown files in `data/{agent_id}/schedules/` (admin) and `workspace/schedules/` (agent-writable). 60s poll loop via `croniter`.
- **Notification inbox** ([docs/notifications.md](docs/notifications.md)) — append-only JSONL under `workspace/notifications/`. Web UI bell is push-driven over WebSocket. Producers fail-open. Channel adapters (Mattermost DM, email, vault page) are EventBus subscribers in `notification_channels/`; adding a channel touches only its module + `notification_channels/__init__.py`. For agent-side wakes on job completion, see [docs/background-wake.md](docs/background-wake.md).
- **Email** ([docs/email.md](docs/email.md)) is dual-surface: the `send_email` tool (allowlist-gated, falls through to confirmation; allowlist is a union of config + per-task `email-recipients` frontmatter) and the email notification channel (its `recipient_addresses` config IS the trust boundary).
- **`LOG_LEVEL=DEBUG`** for verbose logging.

### Context assembly

See [docs/context-composer.md](docs/context-composer.md), [docs/semantic-search.md](docs/semantic-search.md).

- **All context for a turn assembled by `ContextComposer.compose()`** — produces a `ComposedContext` (messages, tools, deferred tools, token estimates, diagnostics). Stateful per-conversation via `ComposerState` on `ctx.composer`. Mode-aware: `INTERACTIVE`, `HEARTBEAT`, `SCHEDULED`, `CHILD_AGENT`. Tool assembly in the iteration loop still uses `_build_tool_list()` since fetched tools change mid-turn.
- **Tool-result clearing tier.** Before each compaction-threshold check, `clear_old_tool_results` (`context_cleanup.py`) replaces bodies of large, old tool messages in-memory with a short stub (`[tool output cleared: N bytes]`). Originals stay in the JSONL archive — only the in-memory view changes. Cheap (no LLM), runs every iteration. Preserve-tools allowlist + recent-N-turns protected window keep `activate_skill`, `checklist_*`, and the current/prior user turn intact. Tunables on `config.cleanup`. See [docs/context-composer.md#tool-result-clearing-lightweight-tier](docs/context-composer.md#tool-result-clearing-lightweight-tier).
- **Memory retrieval** uses composite scoring (`w_similarity * sim + w_recency * rec + w_importance * imp`); dynamic budget allocation fills from top until exhausted. Wiki-link graph expansion follows `[[links]]` one hop. Fail-open — embedding errors silently return empty. Skipped for heartbeat/scheduled/child agents. Disabled silently if no embedding model configured.
- **Vault page frontmatter** (`summary`, `keywords`, `tags`, `importance`) parsed by `frontmatter.py`; LLM-generated, human-editable. Composite embeddings prepend metadata to body.
- **`@[[PageName]]` mentions** inject pages once per conversation as `vault_references` role messages.
- **Diagnostics sidecar** at `workspace/conversations/{conv_id}.context.json`; `GET /api/conversations/{id}/context` serves it; UI popover with waffle chart.

### Agent behavior

- **One agent turn per conversation at a time.** Concurrent conversations are fine.
- **`ConversationManager` owns agent loops.** Transports are thin adapters. All turn orchestration (user, heartbeat, scheduled, child-agent, wake) routes through `enqueue_turn(kind=...)`. The `TurnKind` enum drives per-kind context/circuit-breaker/persistence policy. Per-conversation busy flag serializes turn kinds.
- **Confirmations are persistent conversation messages** (`role: "confirmation_request"` / `"confirmation_response"`). Pending confirmations survive page reload and server restart (startup scan recovers them). Typed `ConfirmationAction` handlers determine on-approve/on-deny behavior.
- **Transports subscribe to per-conversation event streams** via `manager.subscribe(conv_id, callback)` — not the global bus. Manager bridges global → per-conversation.
- **Self-reflection is fail-open** ([docs/reflection.md](docs/reflection.md)). Skipped for child agents, cancelled turns, empty responses. Retries consume `max_tool_iterations` budget.
- **Pre-compaction memory sweep** runs as an isolated background child agent before compaction summarizes old history. Vault-only tools, fail-open, controlled by `compaction.memory_sweep_enabled`. Prompt at `data/{agent_id}/MEMORY_SWEEP.md` (bundled fallback).

### Mattermost-specific

See [docs/conversations.md](docs/conversations.md), [docs/web-ui.md](docs/web-ui.md), [docs/files-tab.md](docs/files-tab.md), [docs/web-ui-mobile.md](docs/web-ui-mobile.md).

- **Mattermost concerns stay in `mattermost.py`.** Progress formatting, placeholders, threading.
- **Web UI conversation management is REST-only.** WebSocket only for chat streaming, history loading, model changes, cancellation. Workspace files via `/api/workspace/*`. Conversation folders are metadata-only (per-user JSON index); archive files stay in place.
- **Mattermost PATCH quirks.** Omitting `props` preserves existing props (including attachments) — to strip, send `props: {"attachments": []}`. Sending only `props` without `message` clears the text (shows "(message deleted)"). Always include the message text when patching props.
- **Interactive button gotchas.** Button IDs must not contain underscores (callbacks silently dropped). `http_callback_base` must be reachable from MM server's network. Check `AllowedUntrustedInternalConnections` and laptop DHCP IP changes.
- **One bot instance per Mattermost account.** A second silently misses websocket events.

### Workflow

- **Sync with `origin/main` before starting any significant task.** `git fetch origin && git log --oneline main..origin/main`. Stale local main → ghost references and missing docs in audits — every session that skipped this ended with conflicts or rework.
- **Bug fix = test first.** Reproduce with a failing test, then fix.
- **Commit after each logical step.** Lint and test before committing.
- **Iterative changes go in a branch.** Don't push rapid-fire fixes directly to main — regressions compound (especially in UX-sensitive code like streaming/placeholder logic).
- **Test live in Mattermost and the web UI after merging** — real behavior differs from unit tests.
- **Test speed discipline.** Tests run in parallel via `pytest-xdist -n auto`:
  - **Don't `asyncio.sleep(X)` to wait for work.** Wait on the right signal: `await job.reader_task`, `asyncio.wait_for(event.wait(), ...)`, or patch the clock (`_now_iso`/`time.monotonic`). Fixed sleeps are slower *and* flakier.
  - **Anything that runs a real scheduler/timer must patch the work function.** `discover_schedules` picks up bundled scheduled skills (`dream`, `garden`); on a fresh `tmp_path` config they're "never run → due" and fire real `run_agent_turn` calls (one test bled to ~66s this way). Patch `decafclaw.schedules.run_schedule_task` even for "no tasks" scenarios.
- **Check `pytest --durations=25` when adding tests.** Top-25 placement → missing mock or fixed sleep masquerading as a sync primitive.

## Key files

Full doc index: [docs/index.md](docs/index.md). Hot files for navigation:

### Core
- `agent.py` — Agent loop: turn orchestration, tool execution, LLM calls
- `conversation_manager.py` — Central orchestrator: TurnKind dispatch, confirmation persistence, per-conversation event streams
- `context.py` — Forkable runtime context (TokenUsage, ToolState, SkillState, ComposerState)
- `context_composer.py` — Unified context assembly, relevance scoring, dynamic budget allocation
- `events.py` — Pub/sub event bus
- `runner.py` — Top-level orchestrator: MCP, HTTP, Mattermost, heartbeat as parallel tasks
- `confirmations.py` — Confirmation types and handler registry

### Config and LLM
- `config.py`, `config_types.py` — Config dataclasses
- `llm/` — Provider abstraction; `llm/providers/{openai_compat,openai,vertex}.py`

### Transports
- `interactive_terminal.py`, `mattermost.py`, `mattermost_display.py`, `mattermost_ui.py`
- `http_server.py` — Starlette/uvicorn: button callbacks, health
- `web/` — Web gateway: `auth.py`, `conversations.py`, `conversation_folders.py`, `websocket.py`, `workspace_paths.py`, `static/` (Lit components + service layer)

### Data and persistence
- `archive.py` — JSONL conversation archive
- `compaction.py` — Summarization + pre-compaction memory sweep
- `context_cleanup.py` — Lightweight clear tier: stubs old large tool messages before compaction
- `canvas.py` — per-conversation canvas state sidecar + state operations
- `persistence.py`, `attachments.py`, `embeddings.py`, `frontmatter.py`, `memory_context.py`, `checklist.py`

### Tools
- `tools/tool_registry.py` — Priority-based classification, deferred catalog
- `tools/search_tools.py` — `tool_search`
- `tools/{core,workspace_tools,conversation_tools,checklist_tools,shell_tools,http_tools,skill_tools,delegate,model_tools,confirmation,health,attachment_tools,email_tools,heartbeat_tools,canvas_tools}.py`
- `preempt_search.py` — Keyword-match for pre-emptive tool promotion

### Skills (bundled)
`skills/{vault,tabstack,dream,garden,project,claude_code,health,postmortem,ingest,background,mcp,newsletter}/`. `vault`, `background`, `mcp` are always-loaded.

### Other
- `prompts/` — System prompt assembly
- `commands.py` — User-invokable commands
- `reflection.py` — Self-reflection (Reflexion pattern)
- `heartbeat.py`, `schedules.py`, `polling.py`
- `notifications.py`, `notification_channels/`, `mail.py`
- `mcp_client.py`
- `media.py`, `widgets.py`, `widget_input.py`, `web/static/widgets/`, `web/static/components/widgets/widget-host.js`
- `web/static/components/canvas-panel.js`, `web/static/lib/canvas-state.js`
- `util.py`, `eval/`

## Running

```
make run          # Interactive mode (stdin/stdout)
make dev          # Auto-restart on file changes (10s graceful shutdown)
make debug        # Debug logging
make run-pro      # gemini-2.5-pro
make lint         # Compile-check
make typecheck    # Pyright
make check-js     # tsc --checkJs
make check        # Lint + typecheck (Python + JS)
make test         # Pytest
make vendor       # Rebuild web UI vendor bundle
make reindex      # Rebuild embedding index
make build-eval-fixtures
make config       # Show resolved config
```

**Only one bot instance can connect to Mattermost at a time** — a second silently misses websocket events. Les likely has `make dev` running; do NOT start `make run`/`dev`/`debug` without checking. Ask Les to kill the existing one if you need to capture logs.

## Project board

[GitHub project board](https://github.com/users/lmorchard/projects/6) — columns: Backlog, Ready, In progress, In review, Done. Fields: Priority (P0/P1/P2), Size (XS/S/M/L/XL).

- Check **Ready** first when picking work.
- Move to **In progress** on start, **In review** on PR, **Done** on merge (or let `Closes #N` auto-close).
- File new issues onto the board with priority and size.

## Dev sessions

Session docs at `docs/dev-sessions/YYYY-MM-DD-HHMM-slug/` (`spec.md`, `plan.md`, `notes.md`).

Protocol: start → brainstorm → **review spec for gaps** → plan → execute (commit per phase) → retro.

## Keeping docs current

`docs/` ([docs/index.md](docs/index.md)) is the source of truth for feature explanations. CLAUDE.md is for conventions and gotchas — push detail into the relevant `docs/` page, not here.

When changing a feature: update its `docs/` page **as part of the same PR**, not a follow-up. Update `CLAUDE.md` only when conventions or the key-files list change. Update `README.md` to stay concise. Update `docs/context-composer.md` for any change to system prompt / tool definitions / context assembly.

End of dev session: review `docs/` pages for accuracy; update key-files list if modules moved.

## Known gaps

- No hard history size limit (compaction helps but archive grows unbounded).
