# Code Quality Review & Refactoring

**Branch:** `refactor/code-quality-review`
**Date:** 2026-03-28
**Goal:** Systematic code quality improvements across the DecafClaw codebase — reduce duplication, decompose god functions, fix bugs, and improve consistency.

---

## 1. Critical Bugs

### 1.1 Duplicate `renderMarkdown()` with different behavior
- **Files:**
  - `src/decafclaw/web/static/components/messages/tool-call-message.js:11-15`
  - `src/decafclaw/web/static/components/messages/assistant-message.js:36-48`
- **Problem:** Both define their own local `renderMarkdown()` that differs from `lib/markdown.js` — missing wiki link extension and `ADD_ATTR` DOMPurify config. Tool call and assistant messages won't render `[[wiki-links]]` correctly.
- **Fix:** Import from `lib/markdown.js` in both files instead of defining locally.

### 1.2 Unbounded regex cache in agent.py
- **File:** `src/decafclaw/agent.py:294-303`
- **Problem:** `_MEDIA_PLACEHOLDER_RE_CACHE` dict accumulates a compiled regex for every filename ever processed. Never cleaned up.
- **Fix:** Use `functools.lru_cache` with a max size, or just compile on the fly (regex compilation is fast for simple patterns).

---

## 2. Cross-Module Duplication

### 2.1 Token estimation (`len(text) // 4`)
- **Files:**
  - `memory_context.py:50`
  - `compaction.py:125`
  - `core.py:136` (tools)
  - `tool_registry.py:18`
- **Fix:** Extract `estimate_tokens(text: str) -> int` to a shared utility module. Consider placing it in a new `src/decafclaw/util.py` or similar.

### 2.2 Context construction boilerplate
- **Files:**
  - `mattermost.py:~514` — manual Context construction with 7+ property assignments
  - `schedules.py:216-235` — same pattern
  - `heartbeat.py:155-161` — same pattern
- **Problem:** Each site manually sets `conv_id`, `user_id`, `history`, `config`, `event_bus`, etc. Easy to forget a field when adding new context properties.
- **Fix:** Add a factory method or builder on `Context` (e.g. `Context.for_conversation(config, conv_id, ...)`) that handles common setup.

### 2.3 Timer/polling loops
- **Files:**
  - `heartbeat.py:236-309` — `run_heartbeat_timer()`
  - `schedules.py:291-358` — `run_schedule_timer()`
- **Problem:** Nearly identical polling patterns (sleep, check interval, execute, write timestamp). Preamble text for task instructions also duplicated (`heartbeat.py:124-128` vs `schedules.py:251-257`).
- **Fix:** Extract a shared `PollingTimer` or `run_periodic()` helper. Consolidate preamble text.

### 2.4 History restoration
- **Files:**
  - `mattermost.py:292-304` — `_restore_from_archive()`
  - `schedules.py` — similar restoration logic
  - `heartbeat.py` — similar restoration logic
- **Fix:** Extract to a shared function, possibly on `archive.py` or a new `history.py`.

### 2.5 MCP dict-vs-object duck typing
- **File:** `mcp_client.py:145-236, 248-339`
- **Problem:** `isinstance(item, dict)` pattern repeated 20+ times across `_convert_mcp_response()`, `_convert_resource_response()`, and `_convert_prompt_response()`. All three functions have similar structure.
- **Fix:** Create a `_get_attr(obj, key, default=None)` helper that handles both dict and object access. Consolidate repeated media-handling logic.

### 2.6 File error handling in workspace tools
- **File:** `src/decafclaw/tools/workspace_tools.py`
- **Problem:** `FileNotFoundError` / `IsADirectoryError` / `PermissionError` / `UnicodeDecodeError` catch blocks repeated 19 times with nearly identical error messages.
- **Fix:** Extract `_handle_file_error(e, path) -> ToolResult` helper.

### 2.7 Frontend `formatTime()` duplication
- **Files:**
  - `components/messages/user-message.js:6-11`
  - `components/messages/assistant-message.js:9-13`
- **Fix:** Extract to `lib/utils.js` and import.

### 2.8 Frontend resize handle duplication
- **File:** `web/static/app.js:282-340`
- **Problem:** Wiki panel and sidebar resize handlers are nearly identical (differ only in element selectors, min/max values, and storage key).
- **Fix:** Extract `setupResizeHandle({ handle, target, minWidth, maxWidth, storageKey })`.

### 2.9 Frontend conversation item rendering
- **File:** `web/static/components/conversation-sidebar.js:235-277`
- **Problem:** Active and archived conversation lists render nearly identical item markup.
- **Fix:** Extract `#renderConversationItem(conv)` helper method.

### 2.10 Reindex functions in embeddings.py
- **File:** `src/decafclaw/embeddings.py:315-395`
- **Problem:** `reindex_all()`, `reindex_conversations()`, `reindex_wiki()` share the same iterate-parse-index-report pattern.
- **Fix:** Extract a common `_reindex_source(source_gen, label)` helper.

### 2.11 Terminal/Web media handlers are nearly identical
- **File:** `src/decafclaw/media.py:130-186`
- **Problem:** `TerminalMediaHandler` and `WebMediaHandler` have identical `save_media()`, both raise `NotImplementedError` for `upload_file()`, both return `""` from `send_with_media()`. Only differ in `strips_workspace_refs` flag.
- **Fix:** Merge into a single `LocalFileMediaHandler` with `strips_workspace_refs` as constructor param.

---

## 3. God Functions & Large Modules

### 3.1 `agent.py:run_agent_turn()` — 350+ lines
- **Problem:** Mixes turn setup, iteration loop, tool execution, deferred tool management, reflection handling, and cleanup.
- **Suggested decomposition:**
  - `_setup_turn()` — effort resolution, memory context, initial messages
  - `_run_iteration()` — single LLM call + tool execution cycle
  - `_handle_reflection()` — reflection-specific logic
  - `_manage_deferred_tools()` — deferred message insertion/removal
- **Note:** This is the riskiest refactor. Should be done carefully with tests.

### 3.2 `mattermost.py:_process_conversation()` — ~135 lines
- **Problem:** History prep, command dispatch, context building, agent invocation, error handling, cleanup all in one function.
- **Suggested decomposition:**
  - `_prepare_conversation_context()` — history, attachments, context setup
  - `_dispatch_or_run_agent()` — command detection vs agent turn
  - Keep error handling at the top level

### 3.3 `mattermost.py:_subscribe_progress()` — ~125 lines
- **Problem:** Single callback with 11 elif branches for different event types. Dense and hard to follow.
- **Fix:** Use a dispatch dict mapping event types to handler methods.

### 3.4 `compaction.py:compact_history()` — 175 lines
- **Problem:** Archive reading, turn splitting, protected tool detection, incremental vs full decision, LLM calls, history mutation, and event publishing.
- **Suggested decomposition:**
  - `_determine_compaction_range()` — figure out what to compact
  - `_summarize_range()` — call LLM to summarize
  - `_apply_compaction()` — mutate history and write archive

### 3.5 `mcp_client.py` — three `_convert_*_response()` functions
- **Problem:** ~200 lines of similar conversion logic with repeated dict/object branching.
- **Fix:** Unify with shared accessor helper (see 2.5).

### 3.6 `conversation-store.js` — ~564 lines
- **Problem:** God object handling conversation CRUD, message history, streaming, tool status, confirmations, context usage, and effort management.
- **Suggested decomposition:** Split into focused stores (e.g. `MessageStore`, `ToolStatusStore`) coordinated by a main `ConversationStore`.

---

## 4. Consistency & Patterns

### 4.1 Auth middleware for http_server.py
- **File:** `src/decafclaw/http_server.py`
- **Problem:** `_require_auth(request)` + `if not username: return JSONResponse(...)` repeated in 9 route handlers.
- **Fix:** Create a Starlette middleware or decorator `@require_auth` that handles the 401 response automatically.

### 4.2 Inconsistent tool return types
- **Problem:** Some tools return plain `str`, others `ToolResult`. Convention says `ToolResult` for errors.
- **Files:** `conversation_tools.py:20`, `memory_tools.py`, others
- **Fix:** Audit all tool functions and ensure error paths use `ToolResult(text="[error: ...]")`.

### 4.3 Circular imports in tools/
- **Files:** `core.py:60,147`, `tool_registry.py:99`, `skill_tools.py:31`, `delegate.py:31`
- **Problem:** Deferred imports with `# deferred: circular dep` comments. Fragile during refactoring.
- **Fix:** Evaluate if restructuring can eliminate cycles. May need to move shared types or the tool registry to break cycles.

### 4.4 Naive datetimes in conversations.py
- **File:** `src/decafclaw/web/conversations.py:158,186,197,209,220`
- **Problem:** 5 instances of `datetime.now().isoformat()` without timezone. Other modules correctly use `datetime.now(timezone.utc)`.
- **Fix:** Use `datetime.now(timezone.utc).isoformat()` consistently.

### 4.5 JSONL reading without error handling
- **File:** `src/decafclaw/archive.py:54,71`
- **Problem:** `json.loads()` on each line with no `JSONDecodeError` handling. One corrupt line crashes the whole read.
- **Fix:** Wrap in try/except, log warning, skip corrupt lines.

### 4.6 Magic numbers and hardcoded values
- `embeddings.py:263` — `WIKI_BOOST = 1.2` (should be configurable)
- `embeddings.py:245` — `fetch_k = top_k * 3` (unexplained multiplier)
- `memory_context.py:37` — `top_k * 2` (unexplained multiplier)
- `todos.py` — checkbox format strings duplicated as magic strings
- Various hardcoded timeouts across modules

### 4.7 Inconsistent private method naming in JS
- **Problem:** Some components use `_method()` (underscore prefix), others use `#method()` (JS private). Should standardize on `#method()` for true privacy.
- **Files:** `wiki-page.js`, `chat-view.js`, others

---

## 5. Architecture Observations

### 5.1 Context object sprawl
- **File:** `src/decafclaw/context.py`
- **Problem:** 31 attributes mixing request-scoped state, config references, tooling info, token counters, skill state, and approval flags. `fork()` does shallow copy so mutable containers are shared. Every new feature tends to add another attribute.
- **Fix:** Group related attributes into sub-objects (e.g., `ctx.tokens.prompt`, `ctx.tools.extra`, `ctx.conversation.id`). This makes `fork()` semantics clearer and reduces the flat namespace.

### 5.2 markdown_vault/tools.py performance
- **File:** `src/decafclaw/skills/markdown_vault/tools.py`
- **Problem:** `_parse()` is called after every line insert/delete. `bulk_move_items` causes O(n) reparsing.
- **Fix:** Add deferred parsing — collect edits in a batch, then reparse once. For `bulk_move_items`, parse after all deletions/insertions rather than after each one.

### 5.3 embeddings.py schema/migration mixing
- **File:** `src/decafclaw/embeddings.py:28-84`
- **Problem:** `_get_db()` is 57 lines doing DB initialization + schema creation + legacy migration.
- **Fix:** Split into `_init_schema()` and `_migrate_legacy()` functions called from `_get_db()`.

---

## 6. Proposed Phases

### Phase 1: Quick Wins (low risk, high value)
- [ ] Fix `renderMarkdown` import bug in tool-call-message.js and assistant-message.js (1.1)
- [ ] Extract shared `estimate_tokens()` utility (2.1)
- [ ] Extract `formatTime()` to JS utils (2.7)
- [ ] Extract resize handle helper in app.js (2.8)
- [ ] Merge Terminal/Web media handlers (2.11)
- [ ] Fix naive datetimes in conversations.py (4.4)
- [ ] Add JSONL error handling in archive.py (4.5)

### Phase 2: Duplication Cleanup (medium risk)
- [ ] Extract file error handler for workspace tools (2.6)
- [ ] Consolidate MCP conversion functions (2.5)
- [ ] Extract conversation item rendering helper (2.9)
- [ ] Consolidate reindex functions in embeddings.py (2.10)
- [ ] Add auth middleware in http_server.py (4.1)
- [ ] Clean up unbounded regex cache (1.2)

### Phase 3: Decomposition (higher risk, needs tests)
- [ ] Decompose `run_agent_turn()` (3.1)
- [ ] Decompose `_process_conversation()` (3.2)
- [ ] Refactor progress subscriber dispatch (3.3)
- [ ] Decompose `compact_history()` (3.4)
- [ ] Extract shared timer/polling from heartbeat+schedules (2.3)
- [ ] Extract context construction factory (2.2)
- [ ] Extract history restoration helper (2.4)

### Phase 4: Architecture & Consistency
- [ ] Context object redesign — group into sub-objects (5.1)
- [ ] ConversationStore decomposition (3.6)
- [ ] Circular import resolution (4.3)
- [ ] markdown_vault deferred parsing optimization (5.2)
- [ ] Standardize JS private method naming (4.7)
- [ ] Audit all tool return types for consistency (4.2)
- [ ] Embeddings schema/migration split (5.3)
- [ ] Magic numbers cleanup (4.6)
