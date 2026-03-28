# Code Quality Review — Implementation Plan

**Branch:** `refactor/code-quality-review`
**Spec:** `spec.md`
**Approach:** 4 phases, ~30 steps. Each step ends with `make check && make test` to verify. Commit after each phase.

---

## Phase 1: Quick Wins

Low-risk changes — bug fixes, simple extractions, one-line fixes. Each step is independently safe.

---

### Step 1.1: Create shared `lib/markdown.js` and fix duplicate `renderMarkdown`

**Context:** `assistant-message.js:36-48` has the full implementation (custom renderer with workspace URL rewriting, DOMPurify ADD_ATTR/ADD_TAGS). `tool-call-message.js:11-15` has a bare-bones version missing all of that. Neither is extracted to a shared module.

**Prompt:**

> Read `src/decafclaw/web/static/components/messages/assistant-message.js` and `src/decafclaw/web/static/components/messages/tool-call-message.js`.
>
> 1. Create `src/decafclaw/web/static/lib/markdown.js` that exports the full `renderMarkdown()` function from assistant-message.js (with the custom renderer for workspace:// URLs and the DOMPurify config with ADD_ATTR/ADD_TAGS/ADD_DATA_URI_TAGS).
> 2. In `assistant-message.js`, remove the local `renderMarkdown` function and the custom `renderer` object. Add `import { renderMarkdown } from '../lib/markdown.js';` at the top.
> 3. In `tool-call-message.js`, remove the local `renderMarkdown` function. Add `import { renderMarkdown } from '../lib/markdown.js';` at the top.
> 4. Make sure both files still import `unsafeHTML` from lit (they already do).
>
> Run `make check-js` to verify.

---

### Step 1.2: Extract shared `formatTime()` to JS utils

**Context:** `user-message.js:7-11` and `assistant-message.js:54-58` both define identical `formatTime(ts)` functions.

**Prompt:**

> Read `src/decafclaw/web/static/components/messages/user-message.js` and `assistant-message.js`.
>
> 1. Create `src/decafclaw/web/static/lib/utils.js` with the `formatTime(ts)` function exported.
> 2. In `user-message.js`, remove the local `formatTime` and add `import { formatTime } from '../lib/utils.js';`.
> 3. In `assistant-message.js`, remove the local `formatTime` and add `import { formatTime } from '../lib/utils.js';`.
>
> Run `make check-js` to verify.

---

### Step 1.3: Extract shared `estimate_tokens()` Python utility

**Context:** `len(text) // 4` appears in 4 places: `memory_context.py:64`, `compaction.py:125`, `tools/core.py:137`, `tools/tool_registry.py:18`.

**Prompt:**

> 1. Create `src/decafclaw/util.py` with:
>    ```python
>    def estimate_tokens(text: str) -> int:
>        """Rough token estimate (~4 chars per token)."""
>        return len(text) // 4 if text else 0
>    ```
> 2. In `compaction.py`, remove the local `estimate_tokens()` function and import from `decafclaw.util`.
> 3. In `memory_context.py`, replace the inline `len(r["entry_text"]) // 4` with `estimate_tokens(r["entry_text"])`.
> 4. In `tools/core.py`, replace the inline `len(text) // 4 if text else 0` with `estimate_tokens(text)`.
> 5. In `tools/tool_registry.py`, replace `sum(len(json.dumps(td)) // 4 ...)` with `sum(estimate_tokens(json.dumps(td)) ...)`.
>
> Run `make check && make test` to verify.

---

### Step 1.4: Merge Terminal/Web media handlers

**Context:** `media.py` has `TerminalMediaHandler` (lines 130-157) and `WebMediaHandler` (lines 159-186) with byte-for-byte identical implementations. Only difference is `strips_workspace_refs` flag.

**Prompt:**

> Read `src/decafclaw/media.py`.
>
> 1. Create `LocalFileMediaHandler(MediaHandler)` that takes `strips_workspace_refs: bool = False` in its constructor. Move the shared implementation (save_media, upload_file raising NotImplementedError, send_with_media returning "") into it.
> 2. Remove `TerminalMediaHandler` and `WebMediaHandler` classes.
> 3. Search the codebase for all references to `TerminalMediaHandler` and `WebMediaHandler` and update them to use `LocalFileMediaHandler` with the appropriate `strips_workspace_refs` value.
>    - `TerminalMediaHandler` was used with default (no workspace ref stripping) → `LocalFileMediaHandler(config)`
>    - `WebMediaHandler` had `strips_workspace_refs = True` → `LocalFileMediaHandler(config, strips_workspace_refs=True)`
>
> Run `make check && make test` to verify.

---

### Step 1.5: Fix naive datetimes in conversations.py

**Context:** 5 instances of `datetime.now().isoformat()` without timezone in `web/conversations.py`.

**Prompt:**

> Read `src/decafclaw/web/conversations.py`.
>
> 1. Add `from datetime import timezone` to imports (if not present).
> 2. Replace all instances of `datetime.now().isoformat()` with `datetime.now(timezone.utc).isoformat()`.
>
> Run `make check && make test` to verify.

---

### Step 1.6: Add JSONL error handling in archive.py

**Context:** `archive.py` has two `json.loads()` calls inside `for line in f:` loops (lines ~54 and ~71) with no JSONDecodeError handling.

**Prompt:**

> Read `src/decafclaw/archive.py`.
>
> 1. In both functions that read JSONL files, wrap `json.loads(line)` in a try/except for `json.JSONDecodeError`. On error, log a warning with the line number and continue to the next line.
> 2. Import `logging` if not already imported.
>
> Run `make check && make test` to verify.

---

### Step 1.7: Clean up unbounded regex cache in agent.py

**Context:** `agent.py:294` has `_MEDIA_PLACEHOLDER_RE_CACHE: dict[str, _re.Pattern] = {}` that grows without bound.

**Prompt:**

> Read `src/decafclaw/agent.py` around lines 294-303 (the `_media_placeholder_pattern` function and cache).
>
> Replace the manual dict cache with `@functools.lru_cache(maxsize=128)` on the function. Remove the module-level `_MEDIA_PLACEHOLDER_RE_CACHE` dict. The function should just take the filename, compile the pattern, and return it — the LRU cache handles memoization.
>
> Run `make check && make test` to verify.

---

### Step 1.8: Commit Phase 1

> Run `make check && make test`. Commit all Phase 1 changes with message:
> "refactor: phase 1 quick wins — shared utils, media handler merge, bug fixes"

---

## Phase 2: Duplication Cleanup

Medium-risk changes — extracting helpers, consolidating patterns. Each step replaces repeated code with a shared implementation.

---

### Step 2.1: Extract file error handler for workspace tools

**Context:** `workspace_tools.py` has 19 catch blocks for FileNotFoundError/IsADirectoryError/PermissionError/UnicodeDecodeError with nearly identical error messages.

**Prompt:**

> Read `src/decafclaw/tools/workspace_tools.py`.
>
> 1. Add a helper function at the top of the file:
>    ```python
>    def _file_error(e: Exception, path: str) -> ToolResult:
>        """Convert common file exceptions to a ToolResult error."""
>        if isinstance(e, FileNotFoundError):
>            return ToolResult(text=f"[error: file not found: {path}]")
>        if isinstance(e, IsADirectoryError):
>            return ToolResult(text=f"[error: path is a directory, not a file: {path}]")
>        if isinstance(e, PermissionError):
>            return ToolResult(text=f"[error: permission denied: {path}]")
>        if isinstance(e, UnicodeDecodeError):
>            return ToolResult(text=f"[error: file is not valid UTF-8 text: {path}]")
>        return ToolResult(text=f"[error: {e}: {path}]")
>    ```
> 2. Replace all the individual catch blocks with a single `except (FileNotFoundError, IsADirectoryError, PermissionError, UnicodeDecodeError) as e: return _file_error(e, path)` pattern.
> 3. Preserve any catch blocks that have genuinely different behavior (e.g., different error message text or additional logic).
>
> Run `make check && make test` to verify.

---

### Step 2.2: Consolidate MCP dict-vs-object accessor pattern

**Context:** `mcp_client.py` has three `_convert_*_response()` functions (lines 164-339) that repeatedly check `isinstance(item, dict)` to branch between dict access and attribute access.

**Prompt:**

> Read `src/decafclaw/mcp_client.py`, focusing on `_convert_mcp_response()`, `_convert_resource_response()`, and `_convert_prompt_response()`.
>
> 1. Add a helper at module level:
>    ```python
>    def _ga(obj, key, default=None):
>        """Get attribute from dict or object uniformly."""
>        if isinstance(obj, dict):
>            return obj.get(key, default)
>        return getattr(obj, key, default)
>    ```
> 2. Refactor the three `_convert_*_response()` functions to use `_ga()` instead of repeated `isinstance(item, dict)` branching. Each function should become significantly shorter.
> 3. Also look for repeated media-handling logic (image base64 conversion, audio handling) across the three functions. If there's a common pattern, extract it to a helper like `_convert_media_content(item)`.
>
> Run `make check && make test` to verify.

---

### Step 2.3: Consolidate reindex functions in embeddings.py

**Context:** `embeddings.py` has `reindex_all()`, `reindex_conversations()`, `reindex_wiki()` that share iterate-parse-index-report pattern.

**Prompt:**

> Read `src/decafclaw/embeddings.py`, focusing on the reindex functions (approximately lines 315-395).
>
> 1. Extract a shared async helper:
>    ```python
>    async def _reindex_entries(entries, label, config):
>        """Reindex a sequence of (source_id, text, source_type, metadata) tuples."""
>        count = 0
>        for source_id, text, source_type, meta in entries:
>            await index_entry(config, source_id, text, source_type, meta)
>            count += 1
>            if count % 10 == 0:
>                print(f"  {label}: {count}...")
>        print(f"  {label}: {count} total")
>        return count
>    ```
> 2. Refactor each reindex function to produce an iterable/generator of `(source_id, text, source_type, metadata)` tuples, then call `_reindex_entries()`.
> 3. Keep the deletion and DB clearing logic in the individual functions — only the indexing loop is shared.
>
> Run `make check && make test` to verify.

---

### Step 2.4: Split embeddings.py `_get_db()` into schema + migration

**Context:** `_get_db()` is ~57 lines mixing DB initialization, schema creation, and legacy migration.

**Prompt:**

> Read `src/decafclaw/embeddings.py`, focusing on `_get_db()` (approximately lines 28-84).
>
> 1. Extract `_init_schema(db)` — the CREATE TABLE and CREATE INDEX statements.
> 2. Extract `_migrate_legacy(db)` — the legacy embedding BLOB migration code.
> 3. Simplify `_get_db()` to: open connection → call `_init_schema(db)` → call `_migrate_legacy(db)` → return db.
>
> Run `make check && make test` to verify.

---

### Step 2.5: Extract resize handle helper in app.js

**Context:** `app.js` has two nearly identical resize handle implementations for sidebar and wiki panel.

**Prompt:**

> Read `src/decafclaw/web/static/app.js`.
>
> 1. In `lib/utils.js` (created in Step 1.2), add and export a `setupResizeHandle({ handle, target, container, minWidth, maxWidth, storageKey, cssVar })` function that encapsulates the mousedown/mousemove/mouseup pattern.
> 2. Replace both resize handle implementations in `app.js` with calls to `setupResizeHandle()`.
>
> Run `make check-js` to verify.

---

### Step 2.6: Extract conversation item rendering helper in sidebar

**Context:** `conversation-sidebar.js` has three nearly identical `.map()` blocks rendering conversation items (active, archived, system).

**Prompt:**

> Read `src/decafclaw/web/static/components/conversation-sidebar.js`.
>
> 1. Add a private method `#renderConversationItem(conv, { isActive, onAction, actionLabel, badge, onDblClick })` that renders a single conversation item div with the appropriate classes, event handlers, and action button/badge.
> 2. Replace the three `.map()` blocks with calls to `#renderConversationItem()` with different options for each list type.
>
> Run `make check-js` to verify.

---

### Step 2.7: Add auth middleware in http_server.py

**Context:** `_require_auth(request)` + 401 check repeated in 9 route handlers.

**Prompt:**

> Read `src/decafclaw/http_server.py`.
>
> 1. Create an `async def require_auth(request)` function that returns the username or raises an HTTPException(401). Or alternatively, create a decorator `@authenticated` that wraps route handlers — extracts username from auth, returns 401 JSON response if not authenticated, otherwise passes `username` as a keyword argument to the wrapped handler.
> 2. Apply the decorator/middleware to all 9 route handlers that currently call `_require_auth()`.
> 3. Remove the old `_require_auth()` function.
>
> Choose whichever approach (middleware vs decorator) fits best with Starlette's patterns. A simple decorator is probably cleanest here.
>
> Run `make check && make test` to verify.

---

### Step 2.8: Commit Phase 2

> Run `make check && make test`. Commit all Phase 2 changes with message:
> "refactor: phase 2 duplication cleanup — workspace errors, MCP accessor, auth middleware, JS helpers"

---

## Phase 3: Decomposition

Higher-risk structural changes. Each step breaks a large function into smaller pieces while preserving exact behavior.

---

### Step 3.1: Decompose `compact_history()`

**Context:** `compaction.py:compact_history()` is 175 lines with 5 logical phases: load/validate, split messages, determine mode, summarize, rebuild history.

**Prompt:**

> Read `src/decafclaw/compaction.py`, focusing on `compact_history()` (lines 188-363).
>
> Extract the logical phases into helper functions. The data flow is sequential:
>
> 1. Extract `_partition_turns(turns, config)` — takes the turn list, returns `(old_turns, protected_turns, recent_turns)` based on protected tool detection and `compaction_recent_turns`.
> 2. Extract `_determine_compaction_mode(archive, old_messages, config)` — determines whether to do incremental or full compaction. Returns a dict/dataclass with `incremental: bool`, `prev_summary: str|None`, `newly_old_text: str|None`, `full_text: str|None`.
> 3. Extract `_rebuild_history(history, summary, protected_messages, recent_messages, config, conv_id)` — clears history, inserts summary + protected + recent, writes compacted sidecar.
>
> The main `compact_history()` should become a ~40-line orchestrator: load archive → split → partition → determine mode → summarize → rebuild → publish events.
>
> Run `make check && make test` to verify.

---

### Step 3.2: Decompose `run_agent_turn()` — extract setup phase

**Context:** `agent.py:run_agent_turn()` is 350+ lines. Start by extracting the setup phase (lines 504-625) which handles skill restoration, effort resolution, user message processing, memory context injection, and message array building.

**Prompt:**

> Read `src/decafclaw/agent.py`, focusing on `run_agent_turn()` lines 488-625.
>
> Extract two helper functions:
>
> 1. `async _setup_turn_state(ctx, config, user_message, history, images)` — handles lines 504-558:
>    - Skill restoration from sidecar
>    - Auto-activation of always-loaded skills
>    - Effort resolution
>    - Returns the resolved config (with effort overrides applied)
>
> 2. `async _prepare_messages(ctx, config, user_message, history, images)` — handles lines 560-625:
>    - Add user message to history (with truncation)
>    - Inject memory context
>    - Archive user message
>    - Build messages array (system prompt + filtered history)
>    - Resolve attachments
>    - Returns `(messages, history)`
>
> Update `run_agent_turn()` to call these at the top. The function signature and return value must not change.
>
> Run `make check && make test` to verify.

---

### Step 3.3: Decompose `run_agent_turn()` — extract reflection phase

**Context:** The reflection handling in `run_agent_turn()` (lines ~701-790) is a self-contained block that evaluates the response and optionally retries.

**Prompt:**

> Read `src/decafclaw/agent.py`, focusing on the reflection phase in `run_agent_turn()` (approximately lines 700-790).
>
> Extract `async _handle_reflection(ctx, config, messages, history, final_text, tool_summaries, iteration)` that:
> - Checks `_should_reflect()` eligibility
> - Builds prior turn summary
> - Calls `evaluate_response()`
> - If passed: returns `(final_text, False)` — text and no-retry
> - If failed with retries left: injects critique into messages, returns `(None, True)` — retry needed
> - If failed with no retries: returns `(final_text, False)` with optional escalation suggestion
>
> Update the main loop in `run_agent_turn()` to call `_handle_reflection()` and act on the retry flag.
>
> Run `make check && make test` to verify.

---

### Step 3.4: Decompose `_process_conversation()` in mattermost.py

**Context:** `_process_conversation()` is ~135 lines handling message prep, command dispatch, context building, agent invocation, and cleanup.

**Prompt:**

> Read `src/decafclaw/mattermost.py`, focusing on `_process_conversation()` (lines 459-593).
>
> Extract:
>
> 1. `_prepare_conversation(self, conv, combined_text, ...)` — lines ~493-536: message preparation, command dispatch check, placeholder sending, history preparation, context building. Returns `(req_ctx, cmd_result)` where `cmd_result` is non-None if a command was dispatched (and the function should return early).
>
> 2. Keep the agent turn invocation and cleanup in `_process_conversation()` as a ~40-line orchestrator: validate → prepare → if command return → run agent turn → finalize.
>
> The goal is to make the main function readable at a glance while keeping the detailed logic in focused helpers.
>
> Run `make check && make test` to verify.

---

### Step 3.5: Refactor progress subscriber dispatch in mattermost.py

**Context:** `_subscribe_progress()` has 11 elif branches in a single callback. Each branch routes an event to the appropriate ConversationDisplay method.

**Prompt:**

> Read `src/decafclaw/mattermost.py`, focusing on `_subscribe_progress()` (lines 687-812).
>
> Refactor the event routing from elif chains to a dispatch dict pattern:
>
> 1. Define individual handler methods on the class or as inner functions for each event type that has non-trivial logic (e.g., `_handle_reflection_result`, `_handle_memory_context`, `_handle_compaction_start`, `_handle_compaction_end`).
> 2. For simple pass-through events (llm_start, tool_start, tool_end, etc.), keep them as lambdas or short functions.
> 3. Build a dispatch dict mapping event_type → handler. The subscriber callback becomes: look up handler in dict, call it.
>
> Run `make check && make test` to verify.

---

### Step 3.6: Extract shared timer/polling from heartbeat + schedules

**Context:** `heartbeat.py:run_heartbeat_timer()` and `schedules.py:run_schedule_timer()` have nearly identical polling patterns (60s poll, shutdown_event.wait, overlap protection, timestamp tracking).

**Prompt:**

> Read `src/decafclaw/heartbeat.py` (lines 233-309) and `src/decafclaw/schedules.py` (lines 288-358).
>
> 1. Create `src/decafclaw/polling.py` with a shared `run_polling_loop(interval, shutdown_event, on_tick)` async function that handles:
>    - While loop with `asyncio.wait_for(shutdown_event.wait(), timeout=interval)`
>    - CancelledError / shutdown handling
>    - Overlap protection (skip tick if previous still running)
>    - Error logging and continuation
>
> 2. Also extract the shared preamble text pattern to a function: `build_task_preamble(task_type: str, task_name: str) -> str`
>
> 3. Refactor `run_heartbeat_timer()` to use `run_polling_loop()` with a callback that checks elapsed time and runs the heartbeat cycle.
>
> 4. Refactor `run_schedule_timer()` to use `run_polling_loop()` with a callback that discovers due tasks and spawns them.
>
> Run `make check && make test` to verify.

---

### Step 3.7: Extract Context factory methods and history restoration

**Context:** Context construction is repeated in mattermost.py, schedules.py, heartbeat.py, websocket.py, and eval/runner.py. History restoration is duplicated in mattermost.py and potentially other places.

**Prompt:**

> Read `src/decafclaw/context.py` and the Context construction call sites:
> - `mattermost.py:514` (command dispatch context)
> - `schedules.py:216-235` (scheduled task context)
> - `heartbeat.py:155-162` (heartbeat section context)
> - `web/websocket.py:460` (web UI conversation context)
>
> 1. Add a class method `Context.for_task(config, event_bus, *, user_id, conv_id, channel_id="", channel_name="", effort="default", skip_reflection=True, skip_memory_context=True, allowed_tools=None, preapproved_tools=None, preapproved_shell_patterns=None)` that constructs a Context pre-configured for scheduled/automated tasks (heartbeat, schedules).
>
> 2. Refactor `schedules.py` and `heartbeat.py` to use `Context.for_task(...)`.
>
> 3. Move `_restore_from_archive()` from `mattermost.py` to `archive.py` as `restore_history(config, conv_id)` — a public function. Update `mattermost.py` to import and call it.
>
> Run `make check && make test` to verify.

---

### Step 3.8: Commit Phase 3

> Run `make check && make test`. Commit all Phase 3 changes with message:
> "refactor: phase 3 decomposition — agent turn, compaction, mattermost, polling loop"

---

## Phase 4: Architecture & Consistency

Structural improvements and consistency passes across the codebase.

---

### Step 4.1: Context object redesign — group into sub-objects

**Context:** Context has 31 flat attributes. Group them into logical sub-objects to make fork() semantics clearer.

**Prompt:**

> Read `src/decafclaw/context.py`.
>
> Group the 31 attributes into sub-dataclasses:
>
> 1. `TokenUsage` — `total_prompt_tokens`, `total_completion_tokens`, `last_prompt_tokens`
> 2. `ToolState` — `extra_tools`, `extra_tool_definitions`, `deferred_tool_pool`, `allowed_tools`, `preapproved_tools`, `preapproved_shell_patterns`, `current_tool_call_id`
> 3. `SkillState` — `activated_skills`, `skill_data`
> 4. `ConversationInfo` — `conv_id`, `channel_id`, `channel_name`, `thread_id`, `user_id`
>
> Keep the remaining attributes (`config`, `event_bus`, `context_id`, `history`, `messages`, `cancelled`, `media_handler`, `on_stream_chunk`, `event_context_id`, `_current_iteration`, `is_child`, `skip_reflection`, `skip_memory_context`, `effort`) flat on Context — they don't group as naturally.
>
> Update `fork()` and `fork_for_tool_call()` to handle sub-objects (shallow copy the sub-dataclasses via `replace()`).
>
> **IMPORTANT:** This changes every `ctx.conv_id` to `ctx.conversation.conv_id`, `ctx.total_prompt_tokens` to `ctx.tokens.total_prompt`, etc. Search the entire codebase for all attribute access patterns and update them. This is a large mechanical change — be thorough.
>
> Run `make check && make test` to verify.

---

### Step 4.2: ConversationStore decomposition

**Context:** `conversation-store.js` is ~564 lines. The `#handleMessage()` switch has 18 cases mixing conversation management, streaming, tool status, and confirmations.

**Prompt:**

> Read `src/decafclaw/web/static/lib/conversation-store.js`.
>
> Extract focused sub-stores that ConversationStore delegates to:
>
> 1. `MessageStore` — manages `#currentMessages`, `#streamingText`, `#hasMore`, `#mergeToolMessages()`, `loadMoreHistory()`, and the message-related switch cases (`conv_history`, `chunk`, `message_complete`).
>
> 2. `ToolStatusStore` — manages `#toolStatus`, `#pendingConfirms`, and the tool-related switch cases (`tool_start`, `tool_status`, `tool_end`, `confirm_request`, `reflection_result`).
>
> Each sub-store should:
> - Be a plain class (not an EventTarget) — ConversationStore remains the single EventTarget
> - Accept a callback for triggering change events on the parent
> - Own its state and mutation logic
>
> ConversationStore keeps conversation list management, WebSocket coordination, effort, and context usage. It delegates message/tool operations to the sub-stores.
>
> Run `make check-js` to verify.

---

### Step 4.3: Circular import resolution in tools/

**Context:** 5 files in tools/ have deferred imports with `# deferred: circular dep` comments.

**Prompt:**

> Read the import sections of these files:
> - `src/decafclaw/tools/core.py`
> - `src/decafclaw/tools/tool_registry.py`
> - `src/decafclaw/tools/__init__.py`
> - `src/decafclaw/tools/skill_tools.py`
> - `src/decafclaw/tools/delegate.py`
>
> Map the circular dependency graph. Identify what each deferred import needs and why.
>
> The typical solution is to move shared types/constants to a leaf module that doesn't import from the cycle. Consider:
> - Moving `TOOL_DEFINITIONS` dict construction out of `__init__.py` into a separate `registry.py` or `definitions.py`
> - Moving shared types (like `ToolResult`) to `util.py` or a `types.py` if they're causing cycles
>
> Fix as many circular imports as possible. For any that remain unavoidable, add a clear comment explaining the cycle.
>
> Run `make check && make test` to verify.

---

### Step 4.4: markdown_vault deferred parsing optimization

**Context:** `markdown_vault/tools.py` calls `_parse()` after every line insert/delete. `bulk_move_items` does O(n) reparsing.

**Prompt:**

> Read `src/decafclaw/skills/markdown_vault/tools.py`, focusing on the `MarkdownDocument` class and its `_parse()` method, `_delete_lines()`, `_insert_lines()`, and `bulk_move_items()`.
>
> 1. Add a `_dirty` flag to `MarkdownDocument`. Set it `True` in `_delete_lines()` and `_insert_lines()` instead of calling `_parse()`.
> 2. Add a `_ensure_parsed()` method that calls `_parse()` only if `_dirty` is True.
> 3. Call `_ensure_parsed()` at the start of any method that reads parsed state (e.g., `find_section()`, `get_items()`, property accessors).
> 4. In `bulk_move_items()`, the batch of deletions and insertions should all happen without reparsing. A single `_parse()` at the end handles it.
> 5. Keep the existing behavior for `_collapse_blank_lines()` — it should still run after edits but only once per batch.
>
> Run `make check && make test` to verify.

---

### Step 4.5: Standardize JS private method naming

**Context:** Some components use `_method()` (underscore), others use `#method()` (JS native private). Should standardize on `#`.

**Prompt:**

> Search all `.js` files under `src/decafclaw/web/static/` for methods/properties using underscore prefix convention (`_methodName`, `this._prop`).
>
> For each component class (LitElement subclass):
> 1. Convert `_method()` to `#method()` for truly private methods
> 2. Convert `this._prop` to `this.#prop` for private instance properties
> 3. **Exception:** Lit reactive properties declared in `static properties` must keep their names (they're part of Lit's API). Only convert properties that are NOT in the `static properties` block.
> 4. **Exception:** Properties prefixed with `_` that are accessed from outside the class (in templates via `this._prop`) should stay as-is or be converted to proper Lit reactive properties.
>
> Run `make check-js` to verify.

---

### Step 4.6: Audit tool return types for consistency

**Context:** Convention says tools should use `ToolResult(text="[error: ...]")` for errors, but some return plain strings.

**Prompt:**

> Search all files under `src/decafclaw/tools/` for tool functions (functions that start with `tool_` or are registered in TOOL_DEFINITIONS).
>
> For each tool function:
> 1. Check if error paths return `ToolResult(text="[error: ...]")` or plain strings.
> 2. Convert any plain string error returns to `ToolResult(text="[error: ...]")`.
> 3. Success paths can remain as plain strings (that's fine per convention).
>
> Run `make check && make test` to verify.

---

### Step 4.7: Magic numbers cleanup

**Context:** Various hardcoded values scattered across modules.

**Prompt:**

> Address these specific magic numbers:
>
> 1. In `embeddings.py`:
>    - `WIKI_BOOST = 1.2` — already a named constant, which is fine. Add a comment explaining why 1.2.
>    - `fetch_k = top_k * 3` — add a comment: "Over-fetch to allow for threshold filtering and wiki boost reranking"
>
> 2. In `memory_context.py`:
>    - `top_k=mc.max_results * 2` — add a comment: "Over-fetch to allow for deduplication and token budget filtering"
>
> 3. In `todos.py`:
>    - Extract `_UNCHECKED = "- [ ] "` and `_CHECKED = "- [x] "` as module-level constants. Use them in all parsing and formatting code.
>
> Run `make check && make test` to verify.

---

### Step 4.8: Commit Phase 4

> Run `make check && make test`. Commit all Phase 4 changes with message:
> "refactor: phase 4 architecture — Context sub-objects, ConversationStore split, JS cleanup, consistency"

---

## Final Step: Squash and PR

> 1. Run full `make check && make test` one final time.
> 2. Squash the 4 phase commits into a clean history (or keep them separate if Les prefers).
> 3. Create PR against main.
