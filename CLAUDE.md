# DecafClaw — Development Conventions

AI agent testbed with Mattermost, web, and terminal clients. Multi-provider LLMs (Vertex/Gemini, OpenAI, LiteLLM-compatible) and Tabstack web tools. Start with [architecture](docs/architecture.md) and the [doc index](docs/index.md).

`AGENTS.md` links to this file. Keep shared instructions below 28 KiB to leave room under Codex's 32 KiB project-instruction budget. Keep feature details in `docs/`; read the linked page before changing that subsystem.

## Architecture

- Event-driven: EventBus pub/sub (`events.py`) decouples tool execution from delivery.
- Forkable runtime context (`context.py`) carries configuration and the event bus, forked per request.
- Async throughout: agent loop, LLM client, and tool execution.
- Per-conversation state: keyed by thread `root_id` or top-level `channel_id`.
- `ConversationManager` owns every agent loop. Transports are thin adapters.

## Workflow

- **All work happens in a worktree**, including one-file documentation changes. Leave the main clone and unrelated edits untouched. Concurrent sessions make shared checkouts unsafe.
- Fetch before significant work: `git fetch origin`, then `git log --oneline main..origin/main`. Branch from `origin/main`, never the main clone's current branch.
- Put worktrees under `.claude/worktrees/{branch}/`. If a branch is already checked out, another session owns it; choose a new branch.
- Each code worktree needs its own venv: run `uv sync` or `make install`. Copy the main clone's `.env` (it points to shared agent data), then set a distinct `HTTP_PORT`. Run `make test` for a clean baseline. Documentation-only work can skip dependency setup and the baseline; say so.
- **Bug fix = test first.** Demonstrate the failure, then fix it.
- Commit each logical step after linting and appropriate tests. **Run `make fmt` before committing or opening a PR.** Use a branch for iterative changes; do not push rapid fixes to main.
- **Open a PR by default**, including small changes. Les reviews before merge. Recommend a PR over local merge if a generic finishing skill presents both. The integration decision remains Les's; merge requires authorization.
- After merging behavior changes, test live in Mattermost and the web UI. Unit tests do not establish live behavior.
- Configure blame once per clone: `git config blame.ignoreRevsFile .git-blame-ignore-revs`.

### Investigation and gates

- Use bounded searches. After 2–3 targeted queries fail to locate a fact, stop varying searches and read the relevant caller/callee top-to-bottom.
- For test failures, inspect the traceback and actual output (`pytest -vv -s`) instead of searching for every possible caller.
- **Compose gates from their Make targets.** `make check` depends on `install-js check-npm-policy check-message-types fmt-check lint typecheck check-js check-browser-assets`. Do not duplicate their commands in `check` or CI.
- When adding a gate, confirm CI invokes its target, inspect the job log for execution, and prove it fails on a deliberate fault in a throwaway file. Pyright exits 0 for warnings alone; reporting a path does not necessarily gate it.
- Tests use `pytest-xdist -n auto`. Never use fixed `asyncio.sleep(X)` waits. Await the task/event or patch the clock.
- Tests running a real scheduler/timer must patch the work function. Patch `decafclaw.schedules.run_schedule_task` even for “no tasks” cases: bundled `dream`/`garden` schedules can be due on fresh data.
- Inspect `pytest --durations=25` when adding tests. Investigate new tests in the top 25 for missing mocks or fixed sleeps.
- Collection is explicitly `testpaths = ["tests", "contrib"]`. Put new tests under those roots. Do not restore root-directory collection: it would collect agent-authored tests under live `data/`. `tests/test_pytest_collection.py` enforces this boundary.

### Evals

See [eval loop](docs/eval-loop.md). Unit tests cover deterministic behavior; evals exercise LLM behavior with real model calls.

- New or sharpened tool description: add a disambiguation case in `evals/tool_choice/`, then run `make eval-tools`.
- Behavior-affecting features (skills, always-loaded tools, routing prompts, commands): add a case in `evals/<theme>.yaml`. Use `expect_tool`, `expect_no_tool`, or `expect_tool_count_by_name`; bound every case with `max_tool_calls` and `max_tool_errors`.
- Skip evals for non-LLM-visible changes: pure refactors, storage changes, or implementation-only tool tweaks.
- For `expect_no_tool` with tight call limits, set `setup.config_overrides: {reflection.enabled: false}`. Reflection retries can invoke tools. Positive `expect_tool` alone can leave reflection on.
- Per-case configuration uses dotted paths under `setup.config_overrides`, applied with recursive `dataclasses.replace`. Do not add bespoke `setup.*` keys for new configuration fields.
- Run `make eval-history` after evals to inspect trends.

## Code style

- Prefer clarity over abstraction. This is an exploration project.
- Keep storage human-readable and crash-recoverable: Markdown, JSONL archives, SQLite embeddings.
- Stdlib imports belong at module level. Function-level imports are for breaking import cycles only.
- **Declare runtime state on its dataclass.** Do not `setattr` undeclared Config/Context fields or read them with fallback `getattr` calls.
- **Never enumerate fields when copying, forking, snapshotting, or serializing.** Hand-maintained lists silently lose new fields. Use:
  - Dataclass copies: `dataclasses.replace(obj, field=...)`; nested changes use nested `replace` calls.
  - Dataclass JSON round-trip: `dataclasses.asdict(obj)` and `Cls(**d)`.
  - Plain-class snapshots/forks (`Context`): `copy.copy(obj)` plus explicit overrides for sub-objects needing fresh state.
  - Tests: iterate `vars(obj)` or `dataclasses.fields(cls)`, never a field allowlist that can omit the same new field.
- Use `asyncio.Lock` for concurrency guards, never boolean flags.
- Fix warnings and traceback noise, including shutdown. Never use bare `except: pass`; catch and log the exception appropriately.
- Do not preserve deprecated code for test compatibility. Rewrite tests to the new path.

## Tools

Read [tools](docs/tools.md), [priority](docs/tool-priority.md), [search](docs/tool-search.md), and [pre-emptive search](docs/preemptive-tool-search.md).

- **Every tool receives `ctx: "Context"` first**, even when unused. Import Context under `if TYPE_CHECKING:` to avoid the context/composer/skill import cycle. An untyped ctx prevents Pyright checking its calls.
- **Quote the annotation. Do not add `from __future__ import annotations` to a skill's `tools.py`.** The loader executes modules outside `sys.modules`; postponed dataclass annotations can crash loading and silently remove the skill from the catalog.
- `execute_tool` detects sync/async; sync tools run through `asyncio.to_thread`. Calls run concurrently with `asyncio.gather` and a semaphore (`max_concurrent_tools`, default 5). Each gets a forked ctx and its own `current_tool_call_id`.
- Return errors as `ToolResult(text="[error: ...]")`, never bare strings. `ToolResult.data` is structured output, rendered as fenced JSON.
- Group tools by noun, not verb (`conversation_search` and `conversation_compact` share a module).
- Descriptions control LLM behavior. Validate wording changes with the tool-choice eval.
- Shell tools must use shared `check_shell_approval()` in `shell_tools.py`; do not duplicate approval logic.
- Non-MCP tools have a per-call timeout (default 180s, `TOOL_TIMEOUT_SEC`). Definitions override it with `timeout`; `None` opts out. Confirmation-gated/unbounded tools opt out; confirmation waits pause watchdogs. MCP uses per-server timeouts. See the tool docs for the exemption list.
- `ToolResult(end_turn=True)` ends the turn after one final no-tools LLM call. Review gates use `EndTurnConfirm(message=..., on_approve=..., on_deny=...)`.
- Input widgets (`accepts_input=true`) pair with `end_turn=True`. The loop promotes this to `WidgetInputPause`, pauses through confirmation infrastructure, and resumes with a synthetic user selection. Batch precedence: `WidgetInputPause` > `EndTurnConfirm` > `True`.
- Always-loaded checklist tools iterate within one turn: step → `checklist_step_done` → next. Create/done/abort emit the sticky `progress_tracker` fail-open. See [widgets](docs/widgets.md#progress_tracker-widget).
- Publish progress with `ctx.publish("tool_status", ...)`.

## Web UI

Read [web UI design](docs/web-ui-design.md), [web UI](docs/web-ui.md), and [mobile UI](docs/web-ui-mobile.md).

- Use shared primitives in `primitives.css` before adding component-specific border, radius, shadow, or hover rules: `.dc-floating-btn`, `.dc-overlay-header`, `.dc-overlay-close-x`, `.dc-icon-btn`.
- Pico v2 re-scopes `--pico-color` and `--pico-background-color` inside buttons to button colors. Use `color: inherit` or a non-aliased variable such as `--pico-card-background-color`.
- `--pico-secondary-background` is a text color, not a panel background.
- Tag-qualify custom button rules (`button.foo`); `.foo` loses to Pico's `button:not(...)` specificity.
- Never bind `.value` on a `<select>` whose options come from a nested Lit expression. The value commits before options exist. Use `?selected=${...}` on each option (`conversation-sidebar.js`).
- Scope panel selectors to intended children. `.sched-md input[type="text"]` can override nested component styles; use `.sched-md label > input[type="text"]` where appropriate.
- Themes have two axes: `data-theme` × `data-palette`, managed by `lib/theme.js`. Only palettes may set `--pico-primary-background`/`-inverse`, under `[data-palette]`.
- Hand-written `static/` code is served raw. Import browser-loadable files. `make vendor` bundles dependencies; `make gen-api-client` generates OpenAPI types and `lib/api-client/index.js`.
- `make check-js` regenerates the client before TypeScript checking. TypeScript's bundler resolution can accept missing JS imports; the static module-graph test and browser scenarios cover the served assets.
- Add WebSocket wire types to `src/decafclaw/web/message_types.json` and run `make gen-message-types`. Use Python `WSMessageType.X` and JS `MESSAGE_TYPES.X`, never string literals. `make check-message-types` detects drift.

## Skills and trust

Read [skills](docs/skills.md) and [schedules](docs/schedules.md).

- Skills are lazy-loaded: the system prompt holds their catalog; `activate_skill` loads bodies/tools. `always-loaded: true` opts out of deferral.
- Bundled skills live at `src/decafclaw/skills/`, with required `SKILL.md` and optional `tools.py`. Discovery precedence: workspace > agent-level > bundled > `extra_skill_paths`.
- Use absolute imports (`from decafclaw.skills.X.Y import ...`); the loader has no package context for relative imports.
- Define `SkillConfig` in `tools.py`. Activation resolves it through `load_sub_config` (defaults, `config.skills[name]`, env); `init(config, skill_config)` receives both.
- `user-invocable: true` enables `!name` (Mattermost) or `/name` (web), with `$ARGUMENTS`/`$0`/`$1`, `context: fork`, and `allowed-tools`.
- `SCHEDULE.md` can schedule bundled/admin skills as authored. Extra-path/contrib schedules are forced disabled until the user opts in through `data/{agent_id}/schedules/{name}.md`. Workspace skill directories cannot self-schedule.
- Skill permissions live outside the writable workspace at `data/{agent_id}/skill_permissions.json`.
- **Every capability-granting read of `discovered_skills` must call `skills.grants_capability(info)`.** Workspace skills are agent-writable and highest-precedence. Record each reader's tier decision in `tests/test_discovered_skills_consumers.py`.
- Do not write your own `!= "workspace"` check or reuse `schedules._PREAPPROVAL_TIERS`: schedule preapproval excludes `extra`, while general capability grants include trusted contrib skills. They are different trust partitions.
- Dynamic tools export `get_tools(ctx) -> (dict, list)`.

## Configuration, data, and permissions

Read [configuration](docs/config.md), [data layout](docs/data-layout.md), [providers](docs/providers.md), [model selection](docs/model-selection.md), and [MCP](docs/mcp-servers.md).

- Resolution: dataclass defaults → `data/{agent_id}/config.json` → environment (highest). Dataclasses live in `config.py`/`config_types.py`.
- Admin files (SOUL.md, AGENT.md, USER.md, COMPACTION.md, config.json) live at the agent root and are read-only to file tools. The agent can rewrite USER.md/CUSTOM.md through confirmation-gated `update_system_prompt`. SOUL.md stays protected. Agent-writable files live in `workspace/`.
- System sections use XML wrappers (`soul`, `agent_role`, `user_context`, `custom_context`, `skill_catalog`, `loaded_skills`, `deferred_tools`). Add wrappers during assembly; keep source Markdown plain.
- Providers describe connections; `model_configs` name a model and provider reference. Users choose models; the agent cannot change its own model (cost control).
- MCP servers are global, configured in `data/{agent_id}/mcp_servers.json`. Names use `mcp__<server>__<tool>`. Stdio servers restart with backoff.
- **Use shared `secret_policy.py` for admin protection.** Its path-refusal rule covers all six admin tools. Redaction follows `secret` field annotations, never key-name matching. Admins can extend `secret_policy.refuse_paths`/`redact_paths`. Reject writes containing `<redacted>`. This is extensible harm reduction, not a watertight secret boundary.
- [Vault](docs/vault.md): `vault_write` curates pages; `vault_journal_append` records episodes; `dream` distills journals into pages. Keep Obsidian compatibility.
- [Schedules](docs/schedules.md): cron-style Markdown under admin `schedules/` or writable `workspace/schedules/`, polled every 60 seconds with `croniter`.
- [Notifications](docs/notifications.md): append-only JSONL inbox under `workspace/notifications/`; producers fail-open. Delivery adapters subscribe to EventBus in `notification_channels/`. Adding a channel touches its module and `notification_channels/__init__.py`. See [background wakes](docs/background-wake.md) for job completion.
- [Email](docs/email.md): `send_email` uses the configuration allowlist plus admin/bundled task `email-recipients`, otherwise confirmation. The notification channel's `recipient_addresses` configuration is its trust boundary.
- `LOG_LEVEL=DEBUG` enables verbose logs.

## Context and agent behavior

Read [context composer](docs/context-composer.md), [semantic search](docs/semantic-search.md), [notes](docs/notes.md), [reflection](docs/reflection.md), and [loop breaker](docs/loop-breaker.md).

- `ContextComposer.compose()` owns turn context (messages, tools, estimates, diagnostics), with per-conversation `ComposerState`. Modes: `INTERACTIVE`, `HEARTBEAT`, `SCHEDULED`, `CHILD_AGENT`. Iterations refresh tools through the shared `build_tool_list()` path.
- Preserve prompt prefixes across ordinary turns. Archive hints/status in place; append new injections. Keyword matches suggest discovery without changing native schemas. Explicit capability/configuration changes can reset the prefix. See [cache stability](docs/context-composer.md#prompt-caching-prefix-stability).
- Interactive history stays intact until compaction by default. `cleanup.preserve_prompt_cache: false` opts into in-memory tool-result clearing; original bodies remain archived. See [cleanup](docs/context-composer.md#tool-result-clearing-lightweight-tier).
- Notes are transient conversation facts that survive turns/compaction without becoming vault knowledge. Notes and recent journals inject deltas after a baseline; their cursors reset on compaction. Skip automatic injection in heartbeat/scheduled/child modes. See the linked docs for budgets and switches.
- Memory retrieval combines similarity, recency, and importance, follows wiki links one hop, and fills its budget by score. Embedding failures return empty; no embedding model disables retrieval. Skip it for heartbeat/scheduled/child modes.
- `vault_retrieval.mode`: `always` injects bodies, `headlines` injects summaries for on-demand reads, `on_demand` skips automatic retrieval. Explicit `@[[PageName]]` references inject once per conversation regardless of retrieval mode.
- Vault frontmatter (`summary`, `keywords`, `tags`, `importance`) is human-editable and contributes to embeddings.
- Compaction carries forward a structured decision slice (`decisions`, `open_questions`, `artifacts`) in `decisions.json`. Retain applicable entries verbatim, remove obsolete ones, and add new ones. Parse failure preserves the existing slice. See [decision slices](docs/context-composer.md#decision-slice-through-compaction).
- Context diagnostics live beside the conversation at `context.json`, served by `GET /api/conversations/{id}/context`.
- **One turn per conversation at a time.** Concurrent conversations are fine. Route all turn kinds through `ConversationManager.enqueue_turn(kind=...)`; `TurnKind` selects context, persistence, and circuit-breaker policy.
- Confirmations are persistent `confirmation_request`/`confirmation_response` messages. Recover pending confirmations after reload/restart; typed `ConfirmationAction` handlers own approval/denial behavior.
- Transports subscribe through `manager.subscribe(conv_id, callback)`, never directly to the global bus. Use shared `events.emit_for_ctx(ctx)` for the manager's emitter.
- Reflection is fail-open, skips children/cancelled/empty responses, and consumes the shared iteration budget on retries.
- The loop breaker first injects an ephemeral diagnostic nudge, then hard-stops on a second trip. Keep diagnostic/apology/phantom-call prompt wording in `AGENT.md`, not code.
- Pre-compaction memory sweeps use an isolated, vault-only background child, fail-open. Configuration: `compaction.memory_sweep_enabled`; prompt: agent `MEMORY_SWEEP.md` with a bundled fallback.

## Transports and Mattermost

Read [conversations](docs/conversations.md), [files](docs/files-tab.md), [web UI](docs/web-ui.md), and [terminal](docs/web-terminal.md).

- Mattermost progress, placeholders, and threading stay in `mattermost.py`.
- Web conversation management is REST-only. WebSocket handles chat streaming, history loading, model changes, and cancellation. Workspace files use `/api/workspace/*`. Folders are metadata only; archives stay in place.
- `/terminal` is human-only: no LLM turn or archive write. **Never import `terminals.py` from `tools/` or `skills/`.** A test enforces the boundary.
- Mattermost PATCH: omitting `props` preserves attachments; clear them with `props: {"attachments": []}`. Always include message text when patching props, or Mattermost clears it.
- Mattermost button IDs cannot contain underscores. `http_callback_base` must be reachable from the Mattermost server; check `AllowedUntrustedInternalConnections` and laptop IP changes.
- **One bot instance per Mattermost account.** A second misses WebSocket events. Les can already have `make dev` running: check before starting `make run`/`dev`/`debug`. Ask Les to stop it if live logs require replacing it.

## Navigation and commands

Python modules below are under `src/decafclaw/`. Use [docs/index.md](docs/index.md) for the full subsystem map.

| Area | Entry points |
| --- | --- |
| Agent/runtime | `agent.py`, `conversation_manager.py`, `context.py`, `runner.py`, `events.py` |
| Context | `context_composer.py`, `compaction.py`, `compaction_decisions.py`, `context_cleanup.py`, `memory_context.py` |
| Tools | `tool_definitions.py`, `tool_execution.py`, `tools/tool_registry.py`, `tools/search_tools.py`, `preempt_search.py` |
| Configuration/models | `config.py`, `config_types.py`, `config_watcher.py`, `model_catalog.py`, `llm/` |
| Persistence | `archive.py`, `conversation_paths.py`, `persistence.py`, `approval_state.py`, `attachments.py`, `embeddings.py`, `frontmatter.py` |
| UI/transports | `mattermost*.py`, `http_server.py`, `web/`, `terminals.py`, `canvas.py`, `sticky.py`, `widgets.py` |
| Skills | `skills/` (vault/background/mcp always-loaded); opt-in skills under `contrib/skills/` |
| Other subsystems | `notifications.py`, `notification_channels/`, `mail.py`, `schedules.py`, `scheduled_wakes.py`, `polling.py`, `heartbeat.py` |
| Telemetry | `metrics.py`, `tool_telemetry.py`, `reflection_metrics.py`, `eval/` |

[Workflows](docs/workflows.md) use the durable replay engine in `workflow/` (`engine.py`, `journal.py`, `handle.py`, `resume.py`). The project skill emits a sticky progress tracker while EXECUTING and clears it on DONE or leaving EXECUTING.

Common Make targets (see `Makefile` for the complete list):

| Purpose | Targets |
| --- | --- |
| Run | `run`, `dev`, `debug`, `run-pro`; worktree-only isolated server: `run-dev`, `stop-dev` |
| Python | `lint`, `typecheck`, `fmt`, `fmt-check`, `test` |
| Full gate | `check` (composes its prerequisite targets) |
| Web | `check-js`, `test-js`, `vendor`, `gen-api-client`, `check-browser-assets` |
| TUI | `check-tui`, `test-tui` (separate from `check`) |
| Policy/generated types | `check-npm-policy`, `gen-message-types`, `check-message-types` |
| Evals | `eval`, `eval-tools`, `eval-history`, `build-eval-fixtures` |
| Data | `config`, `reindex`, `prune-embeddings`, `migrate-sidecars`, `migrate-sessions` |
| Worktrees | `prune-worktrees-dry`, `prune-worktrees` |

## Project board

[Board](https://github.com/users/lmorchard/projects/6): Priority P0/P1/P2; Size XS/S/M/L/XL. Check Ready first when picking work. Move work to In progress on start, In review when the PR is ready, and Done on merge (`Closes #N` can close the issue). File new issues on the board with priority and size.

Machine-readable board declaration (exact option names):

- **Owner:** `lmorchard`
- **Number:** `6`
- **Status field:** `Status`
- **Columns:**
  - `ready: Ready`
  - `in_progress: In progress`
  - `in_review: In review`
  - `done: Done`

## Session records and documentation

- Session documents: `docs/dev-sessions/YYYY-MM-DD-HHMM-slug/` (`spec.md`, `plan.md`, `notes.md`). Protocol: brainstorm → review spec for gaps → plan → execute/commit per phase → retro.
- `docs/` is the source of truth for feature explanations. This file holds conventions and gotchas, not a second feature manual.
- Update the affected feature's documentation in the same PR. Keep README concise. Update this file for conventions or moved entry points; keep it below the size target.
- System-prompt, tool-definition, and context-assembly changes also update `docs/context-composer.md`.
- At session end, check doc accuracy and update navigation if modules moved.
