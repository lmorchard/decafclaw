# Code Quality Review — Session Notes

**Started:** 2026-03-28
**Branch:** `refactor/code-quality-review`

## Session Log

- Full codebase review completed across all Python and JS modules
- Findings written to spec.md, verified claims against actual code (61% accuracy on first pass — corrected)
- Plan written with 4 phases, executed all phases

## Completed

### Phase 1: Quick Wins
- Extracted `renderMarkdown()` to shared `lib/markdown.js` (fixes wiki link rendering bug)
- Extracted `formatTime()` to shared `lib/utils.js`
- Extracted `estimate_tokens()` to shared `util.py` (was in 4 modules)
- Merged `TerminalMediaHandler` + `WebMediaHandler` → `LocalFileMediaHandler`
- Fixed naive datetimes in `conversations.py` (now UTC)
- Added JSONL error handling in `archive.py`
- Replaced unbounded regex cache with `lru_cache` in `agent.py`

### Phase 2: Duplication Cleanup
- Extracted `_file_error()` helper in `workspace_tools.py` (13 catch blocks consolidated)
- Added `_ga()` uniform accessor in `mcp_client.py` (eliminates dict-vs-object branching)
- Consolidated reindex functions in `embeddings.py` with `_reindex_entries()`
- Split `_get_db()` into `_init_schema()` + `_migrate_legacy()`
- Extracted `setupResizeHandle()` to `lib/utils.js`
- Extracted `#renderConversationItem()` in `conversation-sidebar.js`
- Added `@_authenticated` decorator in `http_server.py`

### Phase 3: Decomposition
- Decomposed `run_agent_turn()`: extracted `_setup_turn_state()`, `_prepare_messages()`, `_handle_reflection()`
- Decomposed `compact_history()`: extracted `_partition_turns()`, `_determine_compaction_mode()`, `_rebuild_history()`
- Decomposed `_process_conversation()`: extracted `_prepare_conversation()`
- Refactored `_subscribe_progress()` from elif chain to dispatch dict
- Created `polling.py` with shared `run_polling_loop()` + `build_task_preamble()`
- Added `Context.for_task()` factory method
- Moved `restore_history()` to `archive.py` as public function

### Phase 4: Architecture & Consistency
- Split `ConversationStore` into `MessageStore` + `ToolStatusStore` sub-stores
- Added deferred parsing to `MarkdownDocument` (batch edits reparse once)
- Audited tool return types (wrapped error paths in `ToolResult`)
- Added explanatory comments to magic numbers
- Extracted checkbox constants in `todos.py`

## Wave 2: Context Redesign

Tackled the Context sub-objects redesign (originally skipped from Phase 4).

- Chose **Option B**: group internal machinery (tokens, tools, skills) into sub-dataclasses, keep conversation identity fields flat
- Defined `TokenUsage`, `ToolState`, `SkillState` dataclasses in context.py
- Updated `fork_for_tool_call()` with explicit copy semantics: shared tools+skills, fresh tokens
- 26 files updated, 0 old-style references remaining

## Wave 2 additions
- Extracted `interactive_terminal.py` from agent.py (170 lines)
- Extracted `mattermost_display.py` from mattermost.py (383 lines)
- Updated CLAUDE.md key files list

## Not doing (by design)

### Circular import resolution in tools/ (4.3)
Only 4 deferred imports remain, all inside function bodies (idiomatic Python pattern). The cycles are `agent → tools → agent` and `tools/__init__ → tool_registry → tools/__init__`. Breaking them would require moving `run_agent_turn` or `TOOL_DEFINITIONS` to a separate module — big structural change for minimal gain.

### JS private method naming (4.7)
135 occurrences of `this._prop` across 6 component files. Many are Lit reactive properties (declared in `static properties`) which MUST keep underscore prefix. Converting the rest to `#` requires per-property analysis of whether each is accessed in templates. High risk of silent template breakage.

## Stats

4 commits across 4 phases. Net impact across the session:
- New shared modules: `util.py`, `polling.py`, `lib/markdown.js`, `lib/utils.js`, `lib/message-store.js`, `lib/tool-status-store.js`
- Major functions decomposed: `run_agent_turn`, `compact_history`, `_process_conversation`, `_subscribe_progress`
- Duplicate code eliminated: media handlers, token estimation, formatTime, renderMarkdown, reindex functions, workspace error handling, auth checks, resize handles, conversation items
