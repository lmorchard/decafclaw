# Codebase research — programmatic tool calling sandbox

## 1. Tool registration + dispatch

**Registration**
- `src/decafclaw/tool_definitions.py:18-50` — `TOOLS` dict + `TOOL_DEFINITIONS` list merged from core, skill, heartbeat, health, delegate, attachment, email, notification, canvas, notes registries.
- `src/decafclaw/tools/tool_registry.py:12-25` — `Priority` enum (`CRITICAL`, `NORMAL`, `LOW`), rank mapping. Tools declare priority via `"priority"` field.
- `src/decafclaw/tools/tool_registry.py:33-46` — `get_critical_names()` = env override (`config.agent.critical_tools`) ∪ `config.always_loaded_skill_tools`.

**Classification + budget**
- `src/decafclaw/tools/tool_registry.py:70-159` — `classify_tools()` hard floor for critical, fills budget/count with normal then low, rest deferred. Budget = `config.tool_context_budget`, count = `config.agent.max_active_tools` (default 40).
- `src/decafclaw/tool_definitions.py:85-124` — `collect_all_tool_defs()` gathers skill-provided + preloaded skill native + core + MCP tools.
- `src/decafclaw/tool_definitions.py:126-167` — `build_tool_list()` applies classification + allowed_tools filter + appends deferred catalog text.
- `src/decafclaw/tools/tool_registry.py:204-268` — `build_deferred_list_text()` groups by source (Core/Skills/MCP), wraps in `<deferred_tools>`.

**Dynamic per-turn refresh**
- `src/decafclaw/tool_definitions.py:38-83` — `refresh_dynamic_tools()` calls each skill's `get_tools(ctx)` per turn, updates `ctx.tools.extra` + `ctx.tools.extra_definitions`, prunes stale entries.

**Dispatch**
- `src/decafclaw/tools/__init__.py:195-250` — `execute_tool(ctx, name, arguments)`. Checks `ctx.tools.allowed`, routes MCP (`mcp__` prefix) to registry, else skill-provided/global `TOOLS`. Returns `ToolResult`.
- `src/decafclaw/tool_execution.py:210-280` — `execute_single_tool()` runs one call under semaphore, forks ctx (`fork_for_tool_call`), publishes tool_start/tool_end events.
- `src/decafclaw/tool_execution.py:282-364` — `execute_tool_calls()` `asyncio.Semaphore(ctx.config.agent.max_concurrent_tools)`, `gather(return_exceptions=True)`.

**Timeout**
- `src/decafclaw/tools/__init__.py:113-150` — `_resolve_tool_timeout()` walks `ctx.tools.extra_definitions`, `TOOL_DEFINITIONS`, `SEARCH_TOOL_DEFINITIONS` for explicit `timeout`; falls back to `config.agent.tool_timeout_sec`. `<= 0` = disabled.
- `src/decafclaw/tools/__init__.py:53-108` — `_run_with_cancel()` races against cancel + timeout. Cancel > timeout on tie.

**ToolResult shape**
- `src/decafclaw/media.py:68-88` — `ToolResult`: `text` (required), `media`, `display_text`, `display_short_text`, `data` (optional dict), `end_turn` (False/True/`EndTurnConfirm`/`WidgetInputPause`), `widget`.
- `src/decafclaw/tool_execution.py:262-279` — Result rendered as `{"role": "tool", "tool_call_id": ..., "content": text + data JSON, "display_short_text": ..., "widget": ...}`.

**Read-only metadata**
- **No `read_only` field exists on tool definitions.** Read-only enforcement is per-tool inline (shell via `check_shell_approval()`, email via `check_email_approval()`).

---

## 2. Skill system

**Frontmatter contract**
- `src/decafclaw/skills/__init__.py:41-100` — `parse_skill_md()` fields: `name`, `description` (required), `requires.env`, `user-invocable` (default True), `allowed-tools` (CSV; `shell(pattern)` for shell), `context` (inline/fork), `argument-hint`, `model`/`effort`, `required-skills`, `always-loaded` (bundled only), `schedule` (cron), `enabled`, `auto-approve` (bundled only). Body = markdown.

**Discovery order**
- `src/decafclaw/skills/__init__.py:215-292` — `discover_skills()` priority: (1) workspace, (2) agent-level, (3) bundled (`src/decafclaw/skills/`), (4) `extra_skill_paths`. First match wins. `auto_approve`/`always_loaded` stripped from non-bundled.

**Loading `tools.py`**
- `src/decafclaw/tools/skill_tools.py:42-59` — `_load_native_tools()` `importlib.util.spec_from_file_location()`, extracts `TOOLS`, `TOOL_DEFINITIONS`, returns module for `get_tools` retrieval.
- `src/decafclaw/tools/skill_tools.py:61-88` — `_call_init()` checks for `SkillConfig` dataclass; calls `load_sub_config(SkillConfig, raw_dict, prefix)` with `config.skills[name]` + env vars (`SKILLS_{SKILL_NAME_UPPER}`). Invokes `init(config, skill_config)` async or sync.

**Activation**
- `src/decafclaw/skills/__init__.py:294-333` — `build_catalog_text()` separates always-loaded from on-demand.
- `src/decafclaw/tools/skill_tools.py:90-125` — `restore_skills()` re-activates from `ctx.skills.activated` at turn start.
- `src/decafclaw/tools/skill_tools.py:127-172` — `tool_activate_skill()`: requests confirmation unless heartbeat OR `perms[name] == "always"` OR `skill_info.auto_approve`. Permissions at `config.agent_path / skill_permissions.json`.
- `src/decafclaw/tools/skill_tools.py:174-210` — `activate_skill_internal()` loads tools, calls `_call_init()`, registers on `ctx.tools.extra` + dynamic provider if `get_tools()` present, marks active.

---

## 3. Subprocess management (MCP)

**Spawn**
- `src/decafclaw/mcp_client.py:583-603` — `_connect_stdio()` builds `StdioServerParameters(command, args, env)`, uses `mcp.stdio_client()`, enters into `AsyncExitStack`, creates `ClientSession`, calls `initialize()`.

**Discovery + notification handling**
- `src/decafclaw/mcp_client.py:509-571` — `connect_server()` lists tools, wraps each via `_make_tool_caller()`, stores in `state.tools` + `state.tool_definitions`.
- `src/decafclaw/mcp_client.py:481-507` — Notification handler for `ToolListChangedNotification` etc. triggers refresh.

**Reconnect backoff**
- `src/decafclaw/mcp_client.py:660-703` — `_maybe_reconnect()` max_retries=3, backoff `2^retry_count` (min 8s).
- `src/decafclaw/mcp_client.py:628-658` — `_make_tool_caller()` calls `asyncio.wait_for(state.session.call_tool(...), timeout=server_config.timeout / 1000)`.

**Framing**
- JSON-RPC 2.0 over stdio (line-delimited JSON), delegated to `mcp` SDK. Decafclaw doesn't manually frame — only translates `ContentBlock` results via `_convert_mcp_response()` (`mcp_client.py:232-271`).

**Shutdown**
- `src/decafclaw/mcp_client.py:717-756` — `disconnect_server()` exits `_exit_stack`. `disconnect_all()` with 5s timeout.

---

## 4. Confirmation + write-tool gating

**Entry point**
- `src/decafclaw/tools/confirmation.py:106-141` — `request_confirmation(ctx, tool_name, command, message, timeout=60, **extra_event_fields)`. Routes via `ctx.request_confirmation` manager if available, else legacy event-bus. Returns `{"approved": bool}` plus optional `{"always": bool, "add_pattern": bool}`.
- `src/decafclaw/tools/confirmation.py:14-29` — `_get_tool_action_map()` maps `"shell"` → `RUN_SHELL_COMMAND`, `"activate_skill"` → `ACTIVATE_SKILL`, `"end_turn_confirm"` → `CONTINUE_TURN`.

**EndTurnConfirm**
- `src/decafclaw/media.py:17-33` — `EndTurnConfirm(message, approve_label, deny_label, on_approve, on_deny)`. Loop renders buttons; if approved, calls `on_approve()` then continues; if denied, ends.
- `src/decafclaw/tool_execution.py:335-363` — End-turn signal priority in batch: `WidgetInputPause` > `EndTurnConfirm` > `True`.

**Email allowlist**
- `src/decafclaw/tools/email_tools.py:31-58` — `_recipient_allowed()`: case-insensitive exact match OR `@domain.com` suffix.
- `src/decafclaw/tools/email_tools.py:133-158` — `check_email_approval()` unions `config.email.allowed_recipients` + `ctx.tools.preapproved_email_recipients`. All-allowed → pre-approved; else `request_confirmation()`.

**Shell approval tiers**
- `src/decafclaw/tools/shell_tools.py:15-129` — Tiers: (1) heartbeat-admin auto, (2) `"shell"` in `ctx.tools.preapproved`, (3) pattern in `ctx.tools.preapproved_shell_patterns` AND no metacharacters, (4) pattern in `shell_allow_patterns.json`, (5) `request_confirmation()`.
- Metacharacter detection: `;`, `&&`, `||`, `|`, backtick, `$(`, newline.
- Pattern suggestion heuristic: executable + script path kept, args wildcarded.

---

## 5. hermes-agent reference (`/Users/lorchard/devel/hermes-agent/tools/code_execution_tool.py`)

**Entry**
- `code_execution_tool.py:1036-1071` — `execute_code(code, task_id=None, enabled_tools=None) -> str`. Routes to local UDS or remote file-based RPC based on `_get_env_config()["env_type"]`.

**Allowlist**
- `code_execution_tool.py:60-68` — `SANDBOX_ALLOWED_TOOLS = frozenset([web_search, web_extract, read_file, write_file, search_files, patch, terminal])`. Intersected with session `enabled_tools` to generate stubs.

**Local backend (UDS)**
- `code_execution_tool.py:307-366` — Generated `hermes_tools.py` stub: opens AF_UNIX socket (TCP fallback on Windows), `_call(name, args)` sends JSON-RPC over socket, returns parsed result, serialized via `threading.Lock`.
- `code_execution_tool.py:439-557` — `_rpc_server_loop()` in parent thread accepts one client, reads newline-delimited JSON, enforces allowlist + call limit, dispatches via `handle_function_call()`, writes JSON response.

**Remote backend (file RPC)**
- `code_execution_tool.py:371-427` — Stub `_call()` writes `req_{seq:06d}` files (seq via thread-safe lock), polls `res_{seq:06d}` with adaptive backoff (50ms → 250ms), deletes file when done.
- `code_execution_tool.py:699-838` — `_rpc_poll_loop()` polls remote FS via shell commands (`ls`, `cat`, `mv`).

**Env management**
- `code_execution_tool.py:563-663` — `_get_or_create_env()` reuses terminal environment (container/sandbox/SSH/local).
- `code_execution_tool.py:118-154` — `_scrub_child_env()` filters parent env: passthrough vars always pass; names containing KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/PASSWD/AUTH blocked; safe-prefix list (PATH, HOME, USER, LANG, LC_, TERM, TMPDIR, PYTHONPATH, VIRTUAL_ENV, CONDA, HERMES_).

**Limits**
- `code_execution_tool.py:70-74` — `DEFAULT_TIMEOUT = 300` (5min), `DEFAULT_MAX_TOOL_CALLS = 50`, `MAX_STDOUT_BYTES = 50_000`, `MAX_STDERR_BYTES = 10_000`. Per-tool RPC timeout 300s.
- `code_execution_tool.py:444-445` — Server enforces max tool calls; over-limit returns error instead of dispatching.

**Return shape**
- Both backends return JSON string `{"status": "success"|"error"|"timeout"|"interrupted", "output": stdout, "tool_calls_made": int, "duration_seconds": float, "error": str?}`.
- Output truncation: head 40% + tail 60% to fit MAX_STDOUT_BYTES; ANSI stripped, secrets redacted.
- Exit codes: 124 timeout, 130 interrupted.

**Tool error propagation**
- `handle_function_call()` exceptions caught, returned as `{"error": str(exc)}` per call (script sees dict, continues).
