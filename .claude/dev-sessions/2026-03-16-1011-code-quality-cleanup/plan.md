# Code Quality Cleanup — Plan

## Baseline

- 204 tests passing, lint clean
- Branch: `code-quality-cleanup` off `main` at `0ee4fbe`

## Ordering Rationale

We start with foundational changes (tests for core infra, config cleanup, shared helpers) that later steps depend on. Then tackle the big refactors (mattermost.py, agent.py) which are the riskiest changes. Type annotations go last since they're low-risk and touch everything.

Each phase ends with `make lint && make test` to confirm nothing broke.

---

## Phase 1: Tests for Core Infrastructure

**Why first:** These tests become the safety net for all subsequent refactoring. We write tests *before* changing the modules.

### Step 1.1: Unit tests for `events.py`

Test the EventBus in isolation — subscribe, publish, unsubscribe, async + sync subscribers, error isolation.

**Prompt:**
> Write unit tests for `src/decafclaw/events.py` in `tests/test_events.py`. Follow the existing test patterns in conftest.py (use the `ctx` fixture which already has an EventBus).
>
> Test cases:
> - Subscribe a callback and verify it receives published events
> - Unsubscribe and verify callback stops receiving events
> - Multiple subscribers all receive the same event
> - Async subscriber works correctly
> - Sync subscriber works correctly
> - A subscriber that raises an exception does not affect other subscribers
> - Publishing with no subscribers doesn't error
> - Unsubscribing a non-existent ID doesn't error
>
> Run `make test` after to verify.

### Step 1.2: Unit tests for `context.py`

Test Context creation, forking, and attribute inheritance.

**Prompt:**
> Write unit tests for `src/decafclaw/context.py` in `tests/test_context.py`. Use the existing conftest fixtures.
>
> Test cases:
> - Context creates with config and event_bus
> - Context gets a unique context_id
> - Context.fork() creates a child with a new ID
> - Context.fork() shares the same event_bus
> - Context.fork() accepts keyword overrides that become attributes
> - Context.fork() can override config
> - Context.publish() sends an event with context_id included
> - Forked context publishes events independently from parent
>
> Run `make lint && make test` after.

### Step 1.3: Commit

> Commit: "Add unit tests for EventBus and Context"

---

## Phase 2: Config Cleanup

### Step 2.1: Add `_parse_bool()` helper to `config.py`

**Prompt:**
> In `src/decafclaw/config.py`, add a module-level helper function:
>
> ```python
> def _parse_bool(value: str, default: bool = False) -> bool:
>     """Parse a string to boolean. Returns default if empty/None."""
>     if not value:
>         return default
>     return value.strip().lower() in ("true", "1", "yes")
> ```
>
> Then replace all instances of `os.getenv("...", "...").lower() == "true"` in `load_config()` with `_parse_bool(os.getenv("...", ""), default=<current_default>)`.
>
> There are 5 occurrences:
> - `mattermost_ignore_bots` (default True)
> - `mattermost_ignore_webhooks` (default False)
> - `mattermost_require_mention` (default True)
> - `heartbeat_suppress_ok` (default True)
> - `llm_streaming` (default True)
> - `llm_show_tool_calls` (default True)
>
> Run `make lint && make test` after.

### Step 2.2: Commit

> Commit: "Extract _parse_bool helper in config.py"

---

## Phase 3: Shared Confirmation Helper

### Step 3.1: Extract `request_confirmation()` to a shared module

The confirmation pattern is duplicated across `shell_tools.py` (2x) and `skill_tools.py` (1x). All three follow the same structure: create Event, subscribe, publish request, wait with timeout, unsubscribe, return result.

**Prompt:**
> Create a shared helper for the confirmation request pattern.
>
> In `src/decafclaw/tools/confirmation.py`, create:
>
> ```python
> async def request_confirmation(
>     ctx,
>     tool_name: str,
>     command: str,
>     message: str,
>     timeout: float = 60,
>     **extra_event_fields,
> ) -> dict:
>     """Request user confirmation via the event bus.
>
>     Publishes a tool_confirm_request event and waits for a matching
>     tool_confirm_response. Returns the response dict with at least
>     "approved" (bool). Times out after `timeout` seconds with
>     approved=False.
>     """
> ```
>
> The implementation should:
> 1. Create an asyncio.Event and result dict
> 2. Subscribe a callback that matches on context_id and tool name
> 3. Publish the tool_confirm_request event (including any extra_event_fields)
> 4. Wait with timeout
> 5. Unsubscribe in a finally block
> 6. Return the result dict (always has "approved" key; may have "always", "add_pattern")
>
> Then refactor:
> 1. `shell_tools.py` `tool_shell()` lines 104-131 → use `request_confirmation()`
> 2. `shell_tools.py` `tool_shell_patterns()` lines 177-198 → use `request_confirmation()`
> 3. `skill_tools.py` `_request_confirmation()` lines 130-163 → use the shared helper, adapt the return signature
>
> Run `make lint && make test` after.

### Step 3.2: Add tests for the confirmation helper

**Prompt:**
> Write tests for the new `request_confirmation` helper in `tests/test_confirmation.py`.
>
> Test cases:
> - Approved confirmation returns {"approved": True}
> - Denied confirmation returns {"approved": False}
> - Timeout returns {"approved": False}
> - Extra fields (always, add_pattern) are passed through
> - Only matches events for the correct context_id and tool name
> - Subscriber is cleaned up after completion
> - Subscriber is cleaned up after timeout
>
> Use the existing conftest fixtures (ctx has event_bus). Simulate confirmation by publishing a tool_confirm_response event from a separate task.
>
> Run `make lint && make test` after.

### Step 3.3: Commit

> Commit: "Extract shared confirmation request helper, deduplicate shell/skill tools"

---

## Phase 4: Consistent Tool Error Handling

### Step 4.1: Standardize tool returns to use `ToolResult`

Currently `_to_tool_result()` in `tools/__init__.py` normalizes bare strings to `ToolResult`, but the source code is inconsistent. Standardize by having tools return `ToolResult` directly for error cases.

**Prompt:**
> Review these tool modules and add `from ..media import ToolResult` where missing, then wrap error return strings in `ToolResult(text=...)`:
>
> Files to check (only change error returns, not success returns — success strings are fine since `_to_tool_result` handles them):
> - `tools/conversation_tools.py` — line 34 returns bare string on error
> - `tools/memory_tools.py` — any bare error strings
> - `tools/shell_tools.py` — error returns in `tool_shell` and `tool_shell_patterns`
>
> Do NOT change:
> - `tools/core.py` — already uses ToolResult
> - `tools/workspace_tools.py` — already uses ToolResult
> - `skills/tabstack/tools.py` — leave as-is (skill tools go through _to_tool_result anyway)
>
> The pattern should be: error returns use `ToolResult(text="[error: ...]")`, success returns can stay as bare strings.
>
> Run `make lint && make test` after.

### Step 4.2: Commit

> Commit: "Standardize tool error returns to use ToolResult"

---

## Phase 5: Global Mutable State Guards

### Step 5.1: Replace `_heartbeat_running` flag with `asyncio.Lock`

**Prompt:**
> In `src/decafclaw/tools/heartbeat_tools.py`:
>
> Replace the `_heartbeat_running = False` global flag with an `asyncio.Lock`:
>
> ```python
> _heartbeat_lock = asyncio.Lock()
> ```
>
> Rewrite `_guarded_heartbeat()` to use `async with _heartbeat_lock:` instead of the manual flag set/clear. This makes it crash-safe — if the task crashes, the lock releases automatically.
>
> Update `tool_heartbeat_trigger()` to check `_heartbeat_lock.locked()` instead of the boolean flag.
>
> Run `make lint && make test` after.

### Step 5.2: Commit

> Commit: "Replace heartbeat running flag with asyncio.Lock"

---

## Phase 6: Agent.py Decomposition

### Step 6.1: Extract helper functions from `run_agent_turn()`

Break the 145-line function into focused helpers without changing behavior.

**Prompt:**
> Refactor `src/decafclaw/agent.py` `run_agent_turn()` by extracting these helpers:
>
> 1. `_check_cancelled(ctx, history) -> ToolResult | None` — the cancellation check pattern that appears at lines 103-110 and 153-160. Returns a ToolResult if cancelled, None if not.
>
> 2. `_build_tool_list(ctx) -> list` — the tool list assembly at lines 114-119 (base + extra + MCP).
>
> 3. `_call_llm_with_events(ctx, config, messages, tools) -> dict` — the LLM call block at lines 122-131 (publishes events, handles streaming vs non-streaming).
>
> 4. `_execute_tool_calls(ctx, tool_calls, history, messages, pending_media) -> ToolResult | None` — the tool execution loop at lines 151-182. Returns a ToolResult if cancelled mid-tools, None to continue.
>
> Also add a try/except for `json.JSONDecodeError` around `json.loads(tc["function"]["arguments"])` at line 163, returning an error tool result if the LLM sends malformed JSON.
>
> The main `run_agent_turn()` function should become a clean loop that calls these helpers.
>
> Run `make lint && make test` after.

### Step 6.2: Extract helper functions from `run_interactive()`

**Prompt:**
> Refactor `src/decafclaw/agent.py` `run_interactive()` by extracting:
>
> 1. `_setup_interactive_context(ctx)` — lines 219-238 (populate context defaults, media handler, streaming callback)
>
> 2. `_print_banner(config)` — lines 245-257 (print model, tools, skills, MCP info)
>
> 3. `_create_interactive_progress_subscriber(ctx) -> callback` — lines 259-292 (the on_progress async function)
>
> The main `run_interactive()` should become a clear sequence: setup → connect MCP → print banner → subscribe → resume archive → start heartbeat → REPL loop → cleanup.
>
> Run `make lint && make test` after.

### Step 6.3: Commit

> Commit: "Decompose agent.py into focused helper functions"

---

## Phase 7: Mattermost.py Decomposition

This is the biggest and riskiest phase. We do it in small, safe steps.

### Step 7.1: Extract `ConversationState` dataclass

Replace the 11 parallel state dicts with a single dataclass per conversation, managed by a dict.

**Prompt:**
> In `src/decafclaw/mattermost.py`, create a dataclass near the top of the file (after the imports):
>
> ```python
> @dataclass
> class ConversationState:
>     """Per-conversation state tracked during the bot's lifetime."""
>     history: list = field(default_factory=list)
>     skill_state: dict | None = None
>     pending_msgs: list = field(default_factory=list)
>     debounce_timer: asyncio.Task | None = None
>     last_response_time: float = 0
>     busy: bool = False
>     cancel: asyncio.Event | None = None
>     turn_times: list = field(default_factory=list)
>     paused_until: float = 0
> ```
>
> In `run()`, replace the 11 separate dicts (lines 233-244) with:
> ```python
> conversations: dict[str, ConversationState] = {}
> user_last_msg_time: dict[str, float] = {}  # per-user, kept separate
> ```
>
> Add a helper to get-or-create conversation state:
> ```python
> def _get_conv(conv_id: str) -> ConversationState:
>     if conv_id not in conversations:
>         conversations[conv_id] = ConversationState()
>     return conversations[conv_id]
> ```
>
> Update all references in `run()`, `_process_conversation()`, `_debounce_fire()`, `on_message()` to use `_get_conv(conv_id).field` instead of `dict[conv_id]`.
>
> This is a mechanical refactor — behavior should not change.
>
> Run `make lint && make test` after.

### Step 7.2: Extract `CircuitBreaker` class

**Prompt:**
> Extract the circuit breaker logic from `mattermost.py` into a standalone class within the same file:
>
> ```python
> class CircuitBreaker:
>     """Rate-limits agent turns per conversation to prevent runaway loops."""
>
>     def __init__(self, max_turns: int, window_sec: int, pause_sec: int):
>         self.max_turns = max_turns
>         self.window_sec = window_sec
>         self.pause_sec = pause_sec
>
>     def is_tripped(self, conv: ConversationState) -> bool:
>         """Check if the circuit breaker has tripped. Mutates conv.turn_times and conv.paused_until."""
>         ...
>
>     def record_turn(self, conv: ConversationState):
>         """Record an agent turn for tracking."""
>         conv.turn_times.append(time.monotonic())
> ```
>
> Move the logic from `_check_circuit_breaker()` and `_record_turn()` into this class. Create a `self.circuit_breaker = CircuitBreaker(...)` in `MattermostClient.__init__()`.
>
> Update `_process_conversation()` to call `self.circuit_breaker.is_tripped(conv)` and `self.circuit_breaker.record_turn(conv)`.
>
> Run `make lint && make test` after.

### Step 7.3: Split `_process_conversation()` into phases

**Prompt:**
> Split `_process_conversation()` (currently ~189 lines) into clearly named phases. Each phase is a private method on MattermostClient:
>
> 1. `_prepare_history(conv, conv_id, root_id, channel_id, app_ctx) -> list` — lines 338-362: get or create conversation history, handle archive resume and thread forking. Returns the history list.
>
> 2. `_build_request_context(app_ctx, conv, conv_id, channel_id, root_id, placeholder_id) -> Context` — lines 364-408: fork context, set up media handler, restore skill state, set up streaming display, set up cancellation, subscribe to progress. Returns the configured request context. (Also returns streaming_display and cancel_task as a tuple or via attrs on context.)
>
> 3. `_post_response(response, channel_id, root_id, placeholder_id, streaming_display)` — lines 442-473: handle the various response posting paths (media, streamed, fallback).
>
> The main `_process_conversation()` should become roughly:
> ```python
> async def _process_conversation(self, conv_id, channel_id, msgs, app_ctx, conversations):
>     conv = _get_conv(conv_id)
>     # circuit breaker, busy check, cooldown, combine messages (stays inline — short)
>     placeholder_id = await self.send_placeholder(...)
>     history = await self._prepare_history(...)
>     req_ctx, streaming_display, cancel_task = self._build_request_context(...)
>     try:
>         response = await run_agent_turn(req_ctx, combined_text, history)
>     except/finally:
>         # cleanup
>     await self._post_response(...)
> ```
>
> Run `make lint && make test` after.

### Step 7.4: Move `_process_conversation` and `_subscribe_progress` closure state to method parameters

Currently `_process_conversation` and `_subscribe_progress` are closures defined inside `run()` that capture local variables. Convert them to proper methods on `MattermostClient` that receive their dependencies as parameters.

**Prompt:**
> Convert these closures in `run()` to proper methods on `MattermostClient`:
>
> 1. `_process_conversation` — currently a closure capturing `app_ctx`, `conversations`, `user_last_msg_time`, timing constants, etc. Make it an `async def _process_conversation(self, conv_id, channel_id, msgs, app_ctx, conversations)` method. Pass `conversations` dict and `app_ctx` explicitly.
>
> 2. `_debounce_fire` — currently a closure. Make it a method or keep as inner function that delegates to `_process_conversation`.
>
> 3. `on_message` — keep as inner function (it's the websocket callback) but simplify it to just do rate limiting and debounce, delegating to methods.
>
> 4. `_subscribe_progress` — already a method, no change needed.
>
> The goal is that `run()` becomes primarily setup + teardown, with the message processing logic living in testable methods.
>
> Run `make lint && make test` after.

### Step 7.5: Commit

> Commit: "Decompose mattermost.py: ConversationState, CircuitBreaker, split _process_conversation"

---

## Phase 8: Type Annotations

### Step 8.1: Add return type annotations to core modules

**Prompt:**
> Add return type annotations to functions that are missing them in these files. Only add return types — don't change parameter types or add type annotations to local variables.
>
> Files and functions:
>
> **llm.py:**
> - `call_llm()` → `dict`
> - `call_llm_streaming()` → `dict`
>
> **agent.py:**
> - `_conv_id()` → `str`
> - `_archive()` → `None`
> - `_maybe_compact()` → `None`
> - `run_agent_turn()` → already has ToolResult in docstring, add it to signature
> - All new helper functions from Phase 6
>
> **context.py:**
> - `fork()` → `"Context"`  (use string literal for forward ref)
>
> **events.py:**
> - `subscribe()` → already `str`
> - `unsubscribe()` → `None`
> - `publish()` → `None`
> - Fix type hint: `dict[str, callable]` → `dict[str, Callable]` (import from typing)
>
> **config.py:**
> - `_parse_bool()` → `bool`
> - `load_config()` → already `Config`
>
> **tools/confirmation.py:**
> - `request_confirmation()` → `dict`
>
> Run `make lint && make test` after.

### Step 8.2: Add return type annotations to tool modules

**Prompt:**
> Add return type annotations to all tool functions across:
> - `tools/core.py`
> - `tools/memory_tools.py`
> - `tools/todo_tools.py`
> - `tools/conversation_tools.py`
> - `tools/workspace_tools.py`
> - `tools/shell_tools.py`
> - `tools/skill_tools.py`
> - `tools/mcp_tools.py`
> - `tools/heartbeat_tools.py`
>
> For tool functions, the return type is `str` (for functions that return strings) or `ToolResult` (for functions that return ToolResult objects). Private helpers should also get return types.
>
> Run `make lint && make test` after.

### Step 8.3: Commit

> Commit: "Add return type annotations across core and tool modules"

---

## Phase 9: Final Verification

### Step 9.1: Full test + lint pass

**Prompt:**
> Run `make lint && make test` and fix any issues. Then do a manual review:
>
> 1. Check that `mattermost.py` is meaningfully shorter in its longest methods
> 2. Check that `agent.py` `run_agent_turn()` and `run_interactive()` are each under ~60 lines
> 3. Verify no orphaned imports or dead code
> 4. Verify the confirmation helper is used in all 3 places (shell, shell_patterns, activate_skill)

### Step 9.2: Update docs

**Prompt:**
> Update documentation to reflect the refactoring:
>
> 1. `CLAUDE.md` — update Key files list if new modules were added (e.g., `tools/confirmation.py`)
> 2. `CLAUDE.md` — add convention: "Tool error returns should use `ToolResult(text='[error: ...]')`"
> 3. `CLAUDE.md` — add convention: "Use `asyncio.Lock` instead of boolean flags for concurrency guards"
> 4. Session notes — write a summary in `notes.md`

### Step 9.3: Final commit and squash plan

> Commit any remaining changes, then prepare for merge. The commits during this session should be:
> 1. Add unit tests for EventBus and Context
> 2. Extract _parse_bool helper in config.py
> 3. Extract shared confirmation request helper
> 4. Standardize tool error returns to use ToolResult
> 5. Replace heartbeat running flag with asyncio.Lock
> 6. Decompose agent.py into focused helper functions
> 7. Decompose mattermost.py: ConversationState, CircuitBreaker, split _process_conversation
> 8. Add return type annotations across core and tool modules
> 9. Update docs for refactoring changes

---

## Risk Notes

- **Phase 7 (mattermost.py)** is the highest risk. The closure-to-method conversion changes how state flows through the system. Test manually in Mattermost after merging.
- **Phase 6 (agent.py)** is medium risk. The tool loop refactor changes the control flow of the core agent loop.
- **Phases 1-5, 8** are low risk — additive tests, mechanical refactors, type annotations.
- At no point do we change external behavior — all refactoring is internal structure only.
