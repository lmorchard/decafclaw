# Code Quality Round 2 — Implementation Plan

**Branch:** `refactor/code-quality-round2`
**Spec:** `spec.md`
**Approach:** 3 phases. Commit after each phase.

---

## Phase 1: Quick Fixes

All independent, low-risk changes. Can be done in parallel.

---

### Step 1.1: Type hints on context sub-objects

**Prompt:**

> Read `src/decafclaw/context.py`. Update the `ToolState`, `SkillState`, and `TokenUsage` dataclass fields to use parameterized type hints:
>
> ```python
> # ToolState
> extra: dict[str, Any] = field(default_factory=dict)
> extra_definitions: list[dict] = field(default_factory=list)
> deferred_pool: list[dict] = field(default_factory=list)
> preapproved: set[str] = field(default_factory=set)
> preapproved_shell_patterns: list[str]  # already typed
> allowed: set[str] | None = None
>
> # SkillState
> activated: set[str] = field(default_factory=set)
> data: dict[str, Any] = field(default_factory=dict)
> ```
>
> Add `from typing import Any` if not present. Run `make check`.

---

### Step 1.2: Remove dead empty listener in app.js

**Prompt:**

> Read `src/decafclaw/web/static/app.js`. Find the empty event listener `store.addEventListener('change', () => {})` and remove it. Run `make check-js`.

---

### Step 1.3: Fix duplicate store listener in chat-view.js

**Prompt:**

> Read `src/decafclaw/web/static/components/chat-view.js`.
>
> The `updated()` method (line ~99) adds a store listener without removing the old one. Fix by:
> 1. In `updated()`, when `store` changes, remove the old listener first, then add the new one:
>    ```js
>    updated(changedProps) {
>      if (changedProps.has('store')) {
>        const oldStore = changedProps.get('store');
>        if (oldStore) oldStore.removeEventListener('change', this._onStoreChange);
>        if (this.store) {
>          this.store.addEventListener('change', this._onStoreChange);
>          this._onStoreChange();
>        }
>      }
>    }
>    ```
> 2. Remove the `this.store?.addEventListener('change', this._onStoreChange)` from `connectedCallback` — let `updated()` handle it (it fires after first render with all properties).
>
> Run `make check-js`.

---

### Step 1.4: Fix duplicate store listener in conversation-sidebar.js

**Prompt:**

> Read `src/decafclaw/web/static/components/conversation-sidebar.js`.
>
> Same pattern as chat-view.js. Fix `updated()` to remove old listener before adding new one. Remove the listener add from `connectedCallback`. Keep `disconnectedCallback` as-is (it removes the current listener).
>
> Run `make check-js`.

---

### Step 1.5: Commit Phase 1

> Run `make check && make test`. Commit with message:
> "refactor: quick fixes — context type hints, dead code, listener leaks"

---

## Phase 2: JS Store Boundary Fix

---

### Step 2.1: Refactor ToolStatusStore to not mutate MessageStore's array

**Context:** `ToolStatusStore.handleMessage()` receives `currentMessages` (MessageStore's array) and directly pushes/splices into it. This breaks encapsulation.

**Prompt:**

> Read `src/decafclaw/web/static/lib/tool-status-store.js`, `src/decafclaw/web/static/lib/message-store.js`, and `src/decafclaw/web/static/lib/conversation-store.js`.
>
> Refactor so ToolStatusStore never touches MessageStore's array directly:
>
> 1. Add methods to MessageStore for the mutations ToolStatusStore needs:
>    - `pushMessage(msg)` — already exists
>    - `updateLastToolCall(content)` — update last `tool_call` message's content
>    - `replaceLastToolCall(msg)` — replace last `tool_call` message (for tool_end)
>    - `insertBeforeLastUser(msg)` — insert message before the last user message (for memory_context)
>
> 2. Change ToolStatusStore constructor to accept a message store reference instead of raw array:
>    ```js
>    constructor(onChange, ws, messageStore)
>    ```
>
> 3. Update `handleMessage()` to call MessageStore methods instead of direct array mutation:
>    - `tool_start`: `this.#messageStore.pushMessage({...})`
>    - `tool_status` (memory_context): `this.#messageStore.insertBeforeLastUser({...})`
>    - `tool_status` (other): `this.#messageStore.updateLastToolCall(content)`
>    - `tool_end`: `this.#messageStore.replaceLastToolCall({...})`
>    - `reflection_result`: `this.#messageStore.pushMessage({...})`
>
> 4. Remove the `currentMessages` parameter from `handleMessage()`. Update ConversationStore to pass the messageStore reference instead.
>
> 5. Update ConversationStore's `#handleMessage()` to no longer pass `this.#messages.currentMessages` to ToolStatusStore.
>
> Run `make check-js`.

---

### Step 2.2: Commit Phase 2

> Run `make check && make test`. Commit with message:
> "refactor: fix JS store boundary — ToolStatusStore uses MessageStore API instead of direct mutation"

---

## Phase 3: Test Coverage

Each test file is independent. Can be written in parallel.

---

### Step 3.1: Tests for `util.py`

**Prompt:**

> Create `tests/test_util.py` with tests for `estimate_tokens()`:
> - Empty string returns 0
> - Short string: `estimate_tokens("abcd")` returns 1
> - Longer string: `estimate_tokens("a" * 100)` returns 25
> - Whitespace is counted: `estimate_tokens("    ")` returns 1
>
> Run `make test -k test_util`.

---

### Step 3.2: Tests for `polling.py`

**Prompt:**

> Create `tests/test_polling.py` with async tests for:
>
> 1. `build_task_preamble("heartbeat check")` — returns string containing "heartbeat check"
> 2. `build_task_preamble("scheduled task", "my-task")` — returns string containing "my-task"
> 3. `run_polling_loop()` — calls `on_tick` when interval elapses, then exits on shutdown:
>    ```python
>    async def test_polling_loop_calls_tick():
>        shutdown = asyncio.Event()
>        calls = []
>        async def tick():
>            calls.append(1)
>            shutdown.set()  # stop after first tick
>        await run_polling_loop(0.01, shutdown, tick, label="test")
>        assert len(calls) == 1
>    ```
> 4. `run_polling_loop()` continues after tick failure:
>    ```python
>    async def test_polling_loop_survives_tick_error():
>        shutdown = asyncio.Event()
>        calls = []
>        async def bad_tick():
>            calls.append(1)
>            if len(calls) == 1:
>                raise ValueError("boom")
>            shutdown.set()
>        await run_polling_loop(0.01, shutdown, bad_tick, label="test")
>        assert len(calls) == 2  # continued after first failure
>    ```
> 5. `run_polling_loop()` exits immediately if shutdown already set.
>
> Run `make test -k test_polling`.

---

### Step 3.3: Tests for `archive.py:restore_history()`

**Prompt:**

> Read `src/decafclaw/archive.py` — the `restore_history()` function.
>
> Add tests to `tests/test_archive.py` (or create new file `tests/test_restore_history.py`):
>
> 1. `test_restore_history_no_archive` — returns None when no files exist
> 2. `test_restore_history_archive_only` — returns full archive when no compacted sidecar
> 3. `test_restore_history_compacted_only` — returns compacted when no newer archive entries
> 4. `test_restore_history_compacted_plus_newer` — returns compacted + archive entries newer than last compacted timestamp
> 5. `test_restore_history_ignores_corrupt_lines` — verifies JSONL corruption is handled (from Phase 1 fix)
>
> Use `tmp_path` fixture with a fake config that has `workspace_path = tmp_path`.
>
> Run `make test -k test_restore`.

---

### Step 3.4: Tests for `mattermost_display.py`

**Prompt:**

> Read `src/decafclaw/mattermost_display.py`.
>
> Create `tests/test_mattermost_display.py` testing `ConversationDisplay` with a mock Mattermost client. The mock client should record calls to `send()`, `edit_message()`, `delete_message()`, `send_typing()`.
>
> Test cases:
>
> 1. `test_on_llm_start_sends_thinking` — first call edits placeholder with thinking indicator
> 2. `test_on_llm_start_iteration_2_sends_new_post` — second iteration posts a new thinking message
> 3. `test_on_text_complete_non_streaming` — posts complete text as new message
> 4. `test_on_tool_start_posts_tool_message` — posts a tool call indicator
> 5. `test_on_tool_end_edits_tool_message` — edits the tool message with result
> 6. `test_on_tool_end_truncates_long_result` — long results are truncated
> 7. `test_finalize_strips_thinking` — finalize removes thinking suffix from last message
> 8. `test_finalize_deletes_empty_placeholder` — deletes placeholder if no content was posted
>
> Use `AsyncMock` for the client. Set `throttle_ms=0` in tests to avoid timing issues.
>
> Run `make test -k test_mattermost_display`.

---

### Step 3.5: Tests for priority tool functions

**Prompt:**

> Create `tests/test_core_tools.py` testing the tool functions in `src/decafclaw/tools/core.py`:
>
> 1. `test_tool_think_returns_input` — `tool_think(ctx, thought="hello")` returns "hello"
> 2. `test_tool_current_time_returns_iso` — `tool_current_time(ctx)` returns a parseable ISO datetime
> 3. `test_tool_context_stats_format` — `tool_context_stats(ctx)` returns a string with "System prompt", "Tools", "History" sections (mock ctx with messages and config)
>
> Create `tests/test_todo_tools.py` testing `src/decafclaw/tools/todo_tools.py`:
>
> 1. `test_todo_add_creates_item` — adds an item and verifies it appears in list
> 2. `test_todo_complete_checks_item` — marks item complete
> 3. `test_todo_list_shows_items` — lists items with status
> 4. `test_todo_remove_deletes_item` — removes an item
>
> Use `tmp_path` for workspace. Create minimal ctx with `config.workspace_path` and `conv_id`.
>
> Create `tests/test_memory_tools.py` testing `src/decafclaw/tools/memory_tools.py`:
>
> 1. `test_memory_save_creates_file` — saves a memory entry and verifies file exists
> 2. `test_memory_recent_returns_entries` — after saving, recent returns the entry
>
> Use `tmp_path` for workspace. Skip semantic search tests (require embedding model).
>
> Run `make test -k "test_core_tools or test_todo_tools or test_memory_tools"`.

---

### Step 3.6: Commit Phase 3

> Run `make check && make test`. Commit with message:
> "test: add coverage for polling, util, restore_history, mattermost_display, core/todo/memory tools"

---

## Final Step

> Run full `make check && make test`. Push branch, create PR.
