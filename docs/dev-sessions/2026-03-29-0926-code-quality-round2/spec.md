# Code Quality Review — Round 2

**Branch:** `refactor/code-quality-round2`
**Date:** 2026-03-29
**Goal:** Address 5 focused areas from the second-pass code review: test coverage, JS store boundaries, listener leaks, dead code, and type hints on new dataclasses.

---

## 1. Test Coverage for New/Critical Modules

### 1.1 `polling.py` — zero tests
- **File:** `src/decafclaw/polling.py` (~50 lines)
- **Functions:** `run_polling_loop()`, `build_task_preamble()`
- **What to test:**
  - `build_task_preamble()` output format with/without task name
  - `run_polling_loop()` calls `on_tick` at intervals
  - `run_polling_loop()` exits cleanly on shutdown_event
  - `run_polling_loop()` continues after tick failure (logs error)

### 1.2 `util.py` — zero tests
- **File:** `src/decafclaw/util.py` (~7 lines)
- **Function:** `estimate_tokens()`
- **What to test:**
  - Basic estimation: `estimate_tokens("hello world")` returns ~2-3
  - Empty string returns 0
  - None-safe (empty string case)

### 1.3 `archive.py:restore_history()` — extracted but untested
- **File:** `src/decafclaw/archive.py`
- **Function:** `restore_history(config, conv_id)`
- **What to test:**
  - Returns compacted history when available
  - Falls back to full archive when no compacted sidecar
  - Returns None when no archive exists
  - Merges compacted + newer archive entries correctly

### 1.4 `mattermost_display.py` — zero tests, 383 lines
- **File:** `src/decafclaw/mattermost_display.py`
- **Class:** `ConversationDisplay` with 15 async methods
- **What to test (focused on core logic, not HTTP calls):**
  - `on_llm_start()` sends thinking indicator
  - `on_text_chunk()` buffers and throttles edits
  - `on_tool_start()` posts tool message
  - `on_tool_end()` edits tool message with result
  - `finalize()` cleans up thinking placeholder and stop button
  - Throttling behavior (`_throttled_edit` respects timing)

### 1.5 Tool functions — 44 functions across 10 untested modules
- **Priority tools to test (highest impact):**
  - `tools/core.py` — `tool_web_fetch`, `tool_think`, `tool_context_stats`
  - `tools/conversation_tools.py` — `tool_conversation_search`, `tool_conversation_compact`
  - `tools/memory_tools.py` — `tool_memory_save`, `tool_memory_search`
  - `tools/todo_tools.py` — `tool_todo_add`, `tool_todo_complete`, `tool_todo_list`
  - `tools/effort_tools.py` — `tool_set_effort`
- **Lower priority (complex integration, hard to unit test):**
  - `tools/shell_tools.py` — subprocess execution with confirmation
  - `tools/mcp_tools.py` — MCP server delegation
  - `tools/heartbeat_tools.py` — Mattermost HTTP posting

---

## 2. JS Store Boundary Violation

### 2.1 ToolStatusStore directly mutates MessageStore's array
- **File:** `src/decafclaw/web/static/lib/tool-status-store.js`
- **Problem:** Lines 85, 111, 113, 159 — `currentMessages.push()` and `currentMessages.splice()` directly mutate an array owned by MessageStore.
- **Fix:** Replace direct mutation with a callback. ToolStatusStore constructor should accept an `onAddMessage(msg)` and `onInsertMessage(msg, beforeRole)` callback that MessageStore provides. MessageStore owns all mutations of its array.

---

## 3. Duplicate Store Listener Registration (Memory Leak)

### 3.1 chat-view.js adds store listener twice
- **File:** `src/decafclaw/web/static/components/chat-view.js`
- **Problem:** Store listener added in `connectedCallback` AND again in `updated()` when store property changes. Old listener not removed.
- **Fix:** Only register listener in `connectedCallback` / remove in `disconnectedCallback`. Remove the listener setup from `updated()`, or properly remove-then-add when store changes.

### 3.2 conversation-sidebar.js same pattern
- **File:** `src/decafclaw/web/static/components/conversation-sidebar.js`
- **Problem:** Same duplicate listener pattern as chat-view.
- **Fix:** Same approach — register only in lifecycle callbacks.

---

## 4. Dead Code Cleanup

### 4.1 Empty event listener in app.js
- **File:** `src/decafclaw/web/static/app.js`
- **Problem:** `store.addEventListener('change', () => {})` — empty handler, serves no purpose.
- **Fix:** Remove it.

---

## 5. Type Hints on Context Sub-Objects

### 5.1 ToolState/SkillState/TokenUsage fields need parameterized types
- **File:** `src/decafclaw/context.py`
- **Current:**
  ```python
  extra: dict = field(default_factory=dict)
  extra_definitions: list = field(default_factory=list)
  deferred_pool: list = field(default_factory=list)
  activated: set = field(default_factory=set)
  data: dict = field(default_factory=dict)
  ```
- **Fix:**
  ```python
  extra: dict[str, Any] = field(default_factory=dict)
  extra_definitions: list[dict] = field(default_factory=list)
  deferred_pool: list[dict] = field(default_factory=list)
  activated: set[str] = field(default_factory=set)
  data: dict[str, Any] = field(default_factory=dict)
  ```

---

## Proposed Phases

### Phase 1: Quick Fixes (low risk)
- [ ] Type hints on context sub-objects (5.1)
- [ ] Dead code: remove empty listener in app.js (4.1)
- [ ] Fix duplicate store listeners in chat-view.js (3.1)
- [ ] Fix duplicate store listeners in conversation-sidebar.js (3.2)

### Phase 2: JS Store Boundary Fix (medium risk)
- [ ] Refactor ToolStatusStore to use callbacks instead of direct array mutation (2.1)

### Phase 3: Test Coverage (low risk, high value)
- [ ] Tests for `polling.py` (1.1)
- [ ] Tests for `util.py` (1.2)
- [ ] Tests for `archive.py:restore_history()` (1.3)
- [ ] Tests for `mattermost_display.py` (1.4)
- [ ] Tests for priority tool functions (1.5)
