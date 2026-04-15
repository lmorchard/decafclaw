# Implementation Plan: Conversation Manager & Confirmation Redesign

## Overview

Seven phases, each leaving the system working. During migration, transports switch to the manager one at a time — the old event-bus confirmation flow coexists until all transports are migrated, then gets removed.

Heartbeat and scheduled tasks remain direct `run_agent_turn` callers — they're fire-and-forget with no persistent state, no confirmations (heartbeat auto-approves, scheduled tasks pre-activate skills), and no connected viewers. They can migrate later if needed.

---

## Phase 1: Conversation Manager Core

**What this builds:** The manager module with per-conversation state, confirmation types, event stream, and the public API skeleton. No changes to existing code yet — this is pure addition.

**Codebase state after:** New modules exist but nothing uses them. All existing behavior unchanged.

### Prompt

Create two new modules:

**`src/decafclaw/confirmations.py`** — Confirmation types and handler registry.

- `ConfirmationAction` enum: `RUN_SHELL_COMMAND`, `ACTIVATE_SKILL`, `CONTINUE_TURN`, `ADVANCE_PROJECT_PHASE`
- `ConfirmationRequest` dataclass: `confirmation_id` (auto-generated UUID), `action_type` (ConfirmationAction), `action_data` (dict), `message` (str), `approve_label` (str, default "Approve"), `deny_label` (str, default "Deny"), `tool_call_id` (str, optional), `timeout` (float, optional), `timestamp` (str, auto-set)
- `ConfirmationResponse` dataclass: `confirmation_id` (str), `approved` (bool), `always` (bool, default False), `add_pattern` (bool, default False), `timestamp` (str, auto-set)
- `ConfirmationHandler` protocol: `async def on_approve(ctx, request, response) -> dict` and `async def on_deny(ctx, request, response) -> dict` — returns a dict that the agent loop will use (e.g., `{"inject_message": "...", "continue_loop": True}`)
- `ConfirmationRegistry` class: register handlers by action type, dispatch on response. Include default handlers for each action type that replicate current behavior.
- Serialization: `to_archive_message()` on both dataclasses, producing dicts with `role: "confirmation_request"` / `role: "confirmation_response"` suitable for JSONL archive. `from_archive_message(dict)` class methods for deserialization.

**`src/decafclaw/conversation_manager.py`** — The conversation manager.

- `ConversationState` dataclass (replaces both mattermost.py's and websocket.py's scattered state):
  - `conv_id: str`
  - `history: list`
  - `busy: bool` (turn in progress)
  - `pending_messages: list` (queued while busy)
  - `agent_task: asyncio.Task | None`
  - `cancel_event: asyncio.Event | None`
  - `pending_confirmation: ConfirmationRequest | None`
  - `confirmation_event: asyncio.Event | None` (signals when response arrives)
  - `confirmation_response: ConfirmationResponse | None`
  - `skill_state: dict | None` (extra_tools, extra_definitions, activated_skills)
  - `skip_vault_retrieval: bool`
  - `subscribers: set` (event stream callbacks)
  - `active_model: str`

- `ConversationManager` class:
  - Constructor takes `config`, `event_bus`, `confirmation_registry`
  - Internal `_conversations: dict[str, ConversationState]`
  - `_get_or_create(conv_id) -> ConversationState` — lazy init with history load from archive
  - `async send_message(conv_id, text, user_id, ...)` — API stub (implementation in Phase 2)
  - `async respond_to_confirmation(conv_id, confirmation_id, approved, always=False, add_pattern=False)` — API stub
  - `async cancel_turn(conv_id)` — API stub
  - `get_state(conv_id) -> ConversationState` — returns current state including pending confirmation
  - `subscribe(conv_id, callback) -> str` — returns subscription ID
  - `unsubscribe(conv_id, subscription_id)`
  - `async emit(conv_id, event)` — publish event to all subscribers of a conversation
  - `load_history(conv_id) -> list` — load from archive (compacted + newer)

Lint and type-check after. No tests yet (API stubs aren't functional), but ensure imports resolve cleanly.

---

## Phase 2: Agent Loop Integration

**What this builds:** The manager can run agent turns — context setup, history management, streaming, and the new confirmation suspension/resumption flow. The agent loop itself (`agent.py`) gets minimal changes (confirmation requests go through the manager instead of the event bus).

**Builds on:** Phase 1 (manager and confirmation types exist)

**Codebase state after:** The manager is fully functional. Nothing calls it yet — transports still use the old path. Both paths work independently.

### Prompt

Wire the conversation manager to actually run agent turns:

**In `conversation_manager.py`:**

Implement `send_message()`:
1. Get or create conversation state
2. If busy, queue the message and return
3. Set busy, create cancel event
4. Build a `Context` for the turn:
   - Fork from a base context (passed to manager at init, or created from config + event_bus)
   - Set `user_id`, `channel_id`, `conv_id`, `channel_name` from parameters
   - Set `media_handler` from a factory passed by the transport (the manager shouldn't know about Mattermost vs LocalFile — the transport provides this)
   - Restore per-conversation state: `skill_state`, `skip_vault_retrieval`, `active_model`
   - Set `cancelled` to the cancel event
   - Set `on_stream_chunk` to an internal callback that emits `chunk` events via `self.emit()`
5. Load history via `load_history()`
6. Create an `asyncio.Task` that calls `run_agent_turn(ctx, text, history, ...)`
7. On task completion: save skill state back to conversation state, set not-busy, drain pending messages

Implement `async _run_turn(state, ctx, text, history, ...)`:
- Wrap `run_agent_turn()` call
- On completion, emit `turn_complete` event
- On error, emit `error` event
- After turn completes, persist conversation state (skill_state, flags)
- Drain queued messages (recursively call `send_message` for each)

Implement `respond_to_confirmation()`:
1. Look up conversation state, verify `pending_confirmation` matches `confirmation_id`
2. Create `ConfirmationResponse`, persist to archive via `append_message()`
3. Store response on state, set `confirmation_event` to wake the waiting loop
4. Emit `confirmation_response` event to subscribers

Implement `cancel_turn()`:
- Set cancel event, cancel the asyncio task

Implement `async request_confirmation(conv_id, request: ConfirmationRequest) -> ConfirmationResponse`:
- This is what the agent loop calls instead of the old `request_confirmation()` from `tools/confirmation.py`
- Persist request to archive via `append_message(request.to_archive_message())`
- Set `state.pending_confirmation = request`
- Create `state.confirmation_event = asyncio.Event()`
- Emit `confirmation_request` event to subscribers
- Wait on `confirmation_event` with timeout
- On response: dispatch to confirmation handler, return response
- On timeout: create a denial response, persist, return it

**In `agent.py`:**

Add a way for the agent loop to access the manager's `request_confirmation` method. The cleanest approach: put a `request_confirmation` callable on `ctx` (set by the manager when building the context). The agent loop and tools call `ctx.request_confirmation(...)` instead of importing from `tools/confirmation.py`. If `ctx.request_confirmation` is not set (old code path), fall back to the old event-bus pattern. This enables gradual migration.

**In `tools/confirmation.py`:**

Modify `request_confirmation()` to check for `ctx.request_confirmation` first. If present, delegate to it (building a `ConfirmationRequest` from the arguments). If not, use the existing event-bus flow. This makes the migration transparent to tool code — tools keep calling `request_confirmation(ctx, ...)` and it routes to the right place.

**Streaming integration:**

The manager's internal `on_stream_chunk` callback should:
- For `"text"` chunks: emit `{"type": "chunk", "conv_id": conv_id, "text": data}` to subscribers
- For `"done"`: emit `{"type": "stream_done", "conv_id": conv_id}`
- For `"tool_call_start"`: emit `{"type": "tool_call_start", "conv_id": conv_id, "name": data["name"]}`

**Event forwarding:**

Subscribe to the global event bus for events matching the conversation's context_id. Forward them to the conversation's subscribers via `emit()`. This bridges existing agent loop events (tool_start, tool_end, llm_start, etc.) to the per-conversation stream. The subscription is created when the turn starts and removed when it ends.

Write tests for:
- `send_message` starts a turn and emits events
- `respond_to_confirmation` wakes a blocked confirmation
- Message queuing when busy
- Confirmation timeout produces denial
- Cancel turn stops the agent task

Lint and type-check.

---

## Phase 3: WebSocket Transport Adapter

**What this builds:** Refactor `websocket.py` to be a thin adapter over the conversation manager. This is the first transport to migrate and validates the approach. Fixes #235 and #258 for the web UI.

**Builds on:** Phase 2 (manager can run turns and handle confirmations)

**Codebase state after:** Web UI works through the manager. Confirmations are scoped per-conversation and survive reload. Mattermost and terminal still use old path.

### Prompt

Refactor `src/decafclaw/web/websocket.py`:

**Remove from websocket.py:**
- `_run_agent_turn()` function — the manager handles this now
- `_start_agent_turn()` function — replaced by `manager.send_message()`
- `busy_convs`, `pending_msgs`, `cancel_events`, `agent_tasks` from state dict — the manager tracks these
- `on_turn_event()` callback — replaced by manager subscription
- `streaming_buffer` management — the manager handles this
- `conv_flags` — the manager tracks per-conversation flags

**Keep in websocket.py:**
- WebSocket connection management
- Message parsing (`_handle_send_message`, `_handle_select_conv`, `_handle_load_history`, etc.)
- Command dispatch (`dispatch_command`)
- Conversation index management (REST endpoints are separate, this is the WS side)

**New pattern:**

When a WebSocket selects a conversation (`_handle_select_conv`):
1. Subscribe to the conversation's event stream via `manager.subscribe(conv_id, callback)`
2. Check `manager.get_state(conv_id)` for any pending confirmation — if present, send it to the client immediately
3. Store the subscription ID so we can unsubscribe when switching conversations or disconnecting

The subscription callback formats events as WebSocket JSON messages (same format as current `on_turn_event` output — the client shouldn't need changes yet).

When the WebSocket sends a message (`_handle_send_message`):
1. Parse text, attachments, command context as before
2. Call `manager.send_message(conv_id, text, user_id=username, ...)` instead of `_start_agent_turn()`
3. The manager handles queuing, context setup, and turn execution

When the WebSocket sends a confirmation response (`_handle_confirm_response`):
1. Call `manager.respond_to_confirmation(conv_id, confirmation_id, approved, ...)` instead of publishing to event bus

When the WebSocket cancels a turn (`_handle_cancel_turn`):
1. Call `manager.cancel_turn(conv_id)` instead of setting cancel events directly

**Transport-specific context:**

The manager needs transport-specific information it can't know about (media handler, channel name). Use a context factory or callback pattern: when calling `manager.send_message()`, the transport passes a `context_setup` callback that the manager calls with the fresh ctx before starting the turn. For WebSocket, this sets `media_handler = LocalFileMediaHandler(config)`, `channel_name = "web"`, etc.

**Multiple connections:**

`conv_viewers` tracking moves to the subscription model — each WebSocket that selects a conversation subscribes. The manager's `emit()` fans out to all subscribers. When a WebSocket disconnects, unsubscribe all its active subscriptions.

**Update `_handle_load_history`:**

Call `manager.load_history(conv_id)` instead of reading archives directly. The manager may have in-memory history that's newer than the archive.

**Client-side changes (minimal):**

The `confirm_request` WebSocket message format should stay the same so existing UI components work. The key change is that on page load / conversation select, the server now sends any pending confirmation as part of the initial state.

Add to the `conv_selected` response a `pending_confirmation` field if one exists. The client's `ToolStatusStore` should handle this new field and render the confirmation widget.

In `src/decafclaw/web/static/lib/tool-status-store.js`:
- Handle `pending_confirmation` in `conv_selected` messages (same as receiving a `confirm_request`)

In `src/decafclaw/web/static/components/confirm-view.js`:
- No changes needed if the data shape matches

**Wiring:**

The conversation manager needs to be accessible in the WebSocket handler. Pass it through the app state or as a parameter to the handler function. Update `http_server.py` to create and pass the manager.

Test manually:
- Send a message, verify streaming and tool execution work through the manager
- Trigger a shell command that needs approval, verify confirmation appears
- Reload the page, verify confirmation re-appears
- Open two tabs on the same conversation, verify both see the confirmation
- Switch conversations, verify confirmations are scoped correctly
- Respond to confirmation, verify the agent loop resumes

Lint and type-check. Run existing tests to check for regressions.

---

## Phase 4: Interactive Terminal Adapter

**What this builds:** Refactor interactive_terminal.py to use the conversation manager. Simplest adapter — validates that the manager API works for non-WebSocket transports.

**Builds on:** Phase 3 (manager is proven with WebSocket)

**Codebase state after:** Terminal mode works through the manager. Two of three transports migrated.

### Prompt

Refactor `src/decafclaw/interactive_terminal.py`:

**Remove:**
- Direct `run_agent_turn()` call
- Manual history loading from archive
- Manual event bus subscription for progress display

**New pattern:**

`run_interactive()` becomes:
1. Create or get the conversation manager (passed in or created from config + event_bus)
2. Use a fixed `conv_id` (e.g., `"interactive"` as before, or a new UUID per session)
3. Subscribe to the conversation's event stream with a callback that prints to stdout (replaces `_create_interactive_progress_subscriber`)
4. In the REPL loop:
   - Read user input
   - Call `manager.send_message(conv_id, text, user_id=username, context_setup=terminal_context_setup)`
   - Wait for `turn_complete` event (use an asyncio.Event set by the subscriber callback)
   - Print the final response

**Confirmation handling:**

The subscriber callback watches for `confirmation_request` events. When one arrives:
- Print the confirmation message to stdout
- Prompt the user for input (approve/deny/always) via `asyncio.to_thread(input, ...)`
- Call `manager.respond_to_confirmation(conv_id, confirmation_id, approved, ...)`

This replaces the current inline confirmation handler in `_create_interactive_progress_subscriber`.

**Context setup callback:**

```python
def terminal_context_setup(ctx):
    ctx.media_handler = LocalFileMediaHandler(config)
    ctx.channel_name = "interactive"
```

The streaming callback is no longer set by the terminal — the manager sets its own callback that emits events. The terminal's subscriber handles `chunk` events by printing to stdout.

Test manually:
- Run interactive mode, send messages, verify responses
- Trigger a confirmation, verify prompt appears and response works
- Verify history persists across messages in the same session

Lint and type-check.

---

## Phase 5: Mattermost Transport Adapter

**What this builds:** Refactor mattermost.py to use the conversation manager. Most complex adapter — has debouncing, circuit breaker, emoji polling, post editing. Keep all display logic, delegate state and turn management.

**Builds on:** Phase 4 (manager proven with two transports)

**Codebase state after:** All three transports use the manager. Old direct `run_agent_turn` code paths removed from transports.

### Prompt

Refactor `src/decafclaw/mattermost.py`:

**Remove:**
- `ConversationState` dataclass (replaced by manager's state)
- `_conversations` dict
- Direct `run_agent_turn()` calls in `_process_conversation()`
- Manual history loading in `_prepare_history()`
- Per-conversation busy/queue management
- `_save_skill_state()` / skill state restoration
- `_subscribe_progress()` — replaced by manager subscription

**Keep:**
- `MattermostClient` class and WebSocket connection management
- Message parsing (`_on_posted`, `_should_respond`, mention detection)
- `ConversationDisplay` and all display logic (`mattermost_display.py`)
- Emoji reaction polling for confirmations (but route responses through manager)
- HTTP button callbacks for confirmations (but route through manager)
- Circuit breaker logic (move to manager or keep as Mattermost-specific — see below)
- Debounce timer logic

**New pattern:**

When a message arrives (`_on_posted`):
1. Parse the message, determine conv_id (root_id or channel_id) as before
2. Call `manager.send_message(conv_id, text, user_id=..., context_setup=mm_context_setup, ...)`
3. The manager handles queuing, history, and turn execution

**Context setup callback:**
```python
def mm_context_setup(ctx):
    ctx.media_handler = MattermostMediaHandler(self._http, channel_id)
    ctx.channel_name = ""
    ctx.thread_id = root_id
```

**Event subscription:**

On first message to a conversation, subscribe to the manager's event stream. The subscriber creates/manages a `ConversationDisplay` instance and routes events to its callbacks:
- `chunk` → `display.on_text_chunk()`
- `tool_start` → `display.on_tool_start()`
- `tool_status` → `display.on_tool_status()`
- `tool_end` → `display.on_tool_end()`
- `confirmation_request` → `display.on_confirm_request()` (which creates Mattermost post with buttons/emoji hints)
- `message_complete` → `display.on_text_complete()`
- `turn_complete` → cleanup

**Confirmation flow:**

When a confirmation request event arrives, the Mattermost display creates a post with buttons/emoji as before. When the user responds (emoji reaction or HTTP button):
- Instead of publishing `tool_confirm_response` on the event bus, call `manager.respond_to_confirmation(conv_id, confirmation_id, approved, ...)`
- Update `http_server.py`'s `handle_confirm()` to route through the manager too

**Debouncing and circuit breaker:**

These are Mattermost-specific concerns (preventing rapid-fire posts from overwhelming the channel). Options:
1. Keep in the Mattermost adapter as a wrapper around `manager.send_message()`
2. Move into the manager as configurable per-transport policy

Option 1 is simpler and keeps the manager generic. The adapter debounces before calling the manager, and the circuit breaker decides whether to call at all.

**`mattermost_display.py` changes:**

Minimal. The display still receives events and manages Mattermost posts. The only change is where events come from (manager subscription instead of direct event bus subscription) and how confirmations are responded to.

**`mattermost_ui.py` changes:**

The `ConfirmTokenRegistry` and button building stay. But `handle_confirm()` in `http_server.py` needs to call the manager instead of publishing to event bus.

Test manually in Mattermost:
- Send a message, verify streaming and tool execution work
- Trigger shell approval, verify buttons/emoji appear
- Approve via emoji, verify command executes
- Approve via button, verify command executes
- Verify conversation state persists across turns (skill activation, etc.)
- Verify debouncing and circuit breaker still work
- Verify confirmations are scoped to the right conversation

Lint, type-check, run tests.

---

## Phase 6: Startup Recovery & Confirmation Persistence

**What this builds:** On server startup, scan for conversations with interrupted confirmations. Re-create pending state so transports can re-render them.

**Builds on:** Phase 5 (all transports use the manager, confirmations are persisted as archive messages)

**Codebase state after:** Server restart with a pending confirmation is recoverable. All acceptance criteria met.

### Prompt

**In `conversation_manager.py`:**

Add `async startup_scan(self)`:
1. Scan `{workspace}/conversations/*.jsonl` for archives
2. For each, read the last N messages (tail the file, don't load everything)
3. If the last message has `role: "confirmation_request"` with no subsequent `role: "confirmation_response"`, this is an interrupted confirmation
4. Deserialize the request via `ConfirmationRequest.from_archive_message()`
5. Create a `ConversationState` for this conv_id with `pending_confirmation` set
6. Don't create a `confirmation_event` yet — that happens when a transport calls `respond_to_confirmation()`

Add `async recover_confirmation(self, conv_id)`:
- Called when a response comes in for a conversation that has a pending confirmation but no running agent loop
- Dispatch to the confirmation handler's `on_approve`/`on_deny`
- For `run_shell_command`: execute the command, archive the result
- For `activate_skill`: activate the skill (for the next turn — no loop to resume)
- For `continue_turn`: start a new agent turn with the full history (the LLM will see the confirmation exchange and continue)
- For `advance_project_phase`: advance the phase state

**In `runner.py`:**

After creating the conversation manager, call `await manager.startup_scan()` before starting transports. This ensures pending confirmations are in memory before any client connects.

**Transport changes:**

Already handled by the subscription model — when a transport connects and subscribes to a conversation, it calls `get_state()` which returns any pending confirmation. The only new thing is that on startup, there may be conversations with pending confirmations that no transport has connected to yet. That's fine — they sit in memory until a transport connects.

**Edge cases:**
- Multiple interrupted confirmations in the same conversation: only the last one matters (earlier ones timed out or were superseded)
- Very old interrupted confirmations: add a staleness check (e.g., if the request is more than 24 hours old, discard it rather than recovering)
- Confirmation for a conversation that no longer exists: skip it

Write tests for:
- Startup scan finds interrupted confirmations
- Recovery dispatches to correct handler
- Stale confirmations are discarded
- Clean archives (no pending confirmations) produce no state

Lint and type-check.

---

## Phase 7: Cleanup, Tests, and Documentation

**What this builds:** Remove old code paths, dead code, update tests and documentation.

**Builds on:** Phase 6 (everything works through the manager)

**Codebase state after:** Clean codebase, all tests pass, docs updated.

### Prompt

**Remove old confirmation code:**
- `tools/confirmation.py`: Remove the event-bus fallback path. The `request_confirmation()` function should now always delegate to `ctx.request_confirmation()` and raise an error if it's not set (meaning the caller isn't going through the manager).
- `mattermost.py`: Remove `ConversationState` dataclass and `_conversations` dict (now in manager)
- `websocket.py`: Remove `_run_agent_turn`, `_start_agent_turn`, and all state management code that was replaced
- `events.py`: The event bus stays (used for global events, heartbeat, etc.) but `tool_confirm_request` / `tool_confirm_response` events are no longer used for confirmations — remove any code that depends on this pattern
- `mattermost_ui.py`: The token registry stays (HTTP buttons still use tokens) but response routing goes through the manager

**Update tests:**
- Update any existing tests that mock `request_confirmation()` or event-bus confirmation patterns
- Add integration tests for the full confirmation flow through the manager
- Test confirmation persistence and recovery
- Test multi-transport scenarios (if feasible with test harness)

**Update documentation:**
- `CLAUDE.md`: Update conventions section for confirmation patterns, add conversation_manager.py and confirmations.py to key files
- `docs/`: Create a new `docs/conversation-manager.md` page documenting the architecture
- `docs/`: Update any existing docs that reference the old confirmation flow
- `README.md`: Update if the architecture section references the old pattern

**Final verification:**
- `make check` passes (lint + type-check, Python and JS)
- `make test` passes
- Manual testing in web UI: all acceptance criteria
- Manual testing in Mattermost: confirmations work, no regressions
- Manual testing in interactive terminal: confirmations work
- Server restart recovery: works

---

## Risk Notes

- **Biggest risk:** Phase 5 (Mattermost adapter) has the most surface area and the most transport-specific logic. Circuit breaker, debouncing, ConversationDisplay lifecycle — all need careful migration.
- **Migration gap:** During Phases 3-4, Mattermost still uses the old code path. This is fine — the bugs are web-only and Mattermost confirmations already work. But it means we can't remove old confirmation code until Phase 7.
- **Streaming fidelity:** The current streaming paths have subtle buffering logic (WebSocket's `streaming_buffer`, Mattermost's display buffer). The manager's unified event stream needs to preserve the same behavior or transports need to replicate it in their subscribers.
- **Child agent confirmations:** Need to verify that child agent events still route correctly through the manager. The `event_context_id` mechanism needs to work with the manager's per-conversation event forwarding.
