# Concurrent Tool Calls & Delegate Simplification — Plan

## Status: Ready

## Overview

Six phases, each building on the last. Every phase ends with lint + test passing and a commit. The first four phases deliver concurrent tool execution. Phase 5 simplifies the delegate tool. Phase 6 is cleanup and docs.

---

## Phase 1: Add tool_call_id to ctx and events (plumbing)

**Goal**: Thread `tool_call_id` through the system without changing execution behavior. After this phase, all tool events carry the ID but tools still run sequentially.

**Files**: `context.py`, `agent.py`, `confirmation.py`

### Prompt

Read these files for context:
- `src/decafclaw/context.py` — the Context class
- `src/decafclaw/agent.py` — focus on `_execute_tool_calls` (lines 177-214) and the event publishes
- `src/decafclaw/tools/confirmation.py` — `request_confirmation` function

Make these changes:

1. **`context.py`**: Add `current_tool_call_id: str = ""` to `Context.__init__`. Update `publish()` to auto-include `tool_call_id` from `self.current_tool_call_id` when it's set and `"tool_call_id"` is not already in kwargs.

2. **`agent.py`**: In `_execute_tool_calls`, set `ctx.current_tool_call_id = tc["id"]` before each tool call's `tool_start` publish. Add `tool_call_id=tc["id"]` explicitly to the `tool_start` and `tool_end` publish calls as well. Clear `ctx.current_tool_call_id = ""` after each tool completes (in a finally block). This ensures both the explicit event fields AND any `tool_status` events published by tools themselves carry the ID.

3. **`confirmation.py`**: Add `tool_call_id: str = ""` parameter to `request_confirmation`. Include it in the `tool_confirm_request` event publish. Update the `on_confirm` matcher to also match on `tool_call_id` when provided — i.e. if `tool_call_id` was passed, the response must also have that `tool_call_id` to match. Fall back to the existing `context_id` + `tool_name` matching when `tool_call_id` is empty (backward compat). Update call sites in `shell_tools.py` and `skill_tools.py` to pass `tool_call_id=ctx.current_tool_call_id`.

Do NOT change execution from sequential to concurrent yet. Lint and run tests after. This is pure plumbing.

---

## Phase 2: Add config and concurrency infrastructure

**Goal**: Add the `max_concurrent_tools` config field.

**Files**: `config.py`

### Prompt

Read `src/decafclaw/config.py` for context.

1. Add `max_concurrent_tools: int = 5` to the `Config` dataclass, in the "Agent settings" group near `max_tool_iterations`.

2. Add `MAX_CONCURRENT_TOOLS` to `load_config()`: `max_concurrent_tools=int(os.getenv("MAX_CONCURRENT_TOOLS", "5"))`.

That's it for this phase. Lint and run tests.

---

## Phase 3: Rewrite _execute_tool_calls for concurrency

**Goal**: Replace the sequential loop with concurrent execution via `asyncio.gather` + semaphore. This is the core change.

**Files**: `agent.py`

### Prompt

Read `src/decafclaw/agent.py` — focus on `_execute_tool_calls` (the current sequential implementation), `_check_cancelled`, and `_archive`.

### The ctx fork problem

With concurrent execution, multiple coroutines sharing a single `ctx` will race on `ctx.current_tool_call_id`. If tool A sets the ID, yields at an `await`, then tool B sets a different ID, tool A's next `ctx.publish("tool_status", ...)` will get the wrong ID.

**Solution**: Fork `ctx` per tool call. Each concurrent tool gets its own ctx with its own `current_tool_call_id`. The fork must preserve these fields from the parent:
- `context_id` — **must be the same** as parent (events route by this)
- `event_bus` — shared (fork handles this automatically)
- `config` — shared (fork handles this automatically)
- `cancelled` — same cancel event as parent
- `extra_tools`, `extra_tool_definitions` — for execute_tool lookups
- `skill_data` — for skills like vault that read from it
- `allowed_tools` — for tool access control
- `conv_id`, `channel_id` — for archiving and event routing

Add a helper to Context: `def fork_for_tool_call(self, tool_call_id: str) -> Context` that forks with all the above fields copied and `current_tool_call_id` set.

### Rewrite

1. Extract a new async helper `_execute_single_tool(call_ctx, tc, semaphore)` that:
   - Acquires the semaphore
   - Parses `tc["function"]["arguments"]`
   - Publishes `tool_start` with `tool_call_id`
   - Calls `execute_tool(call_ctx, fn_name, fn_args)`
   - Publishes `tool_end` with `tool_call_id`
   - Archives the tool result message via `_archive(call_ctx, tool_msg)`
   - Returns a tuple `(tool_msg_dict, media_list)`
   - On exception, returns an error tool_msg and empty media (does NOT re-raise)
   - Semaphore release and cleanup in a `finally` block

2. Rewrite `_execute_tool_calls` to:
   - Check cancelled once before starting
   - Create semaphore: `sem = asyncio.Semaphore(ctx.config.max_concurrent_tools)`
   - For each tool call: `call_ctx = ctx.fork_for_tool_call(tc["id"])`, create coroutine `_execute_single_tool(call_ctx, tc, sem)`
   - Create explicit `asyncio.Task` objects for each coroutine (we need task handles for cancellation)
   - Monitor the cancel event in parallel: if it fires, cancel all in-flight tasks
   - Use `asyncio.gather(*tasks, return_exceptions=True)` — but first set up a cancel watcher task that calls `task.cancel()` on each task when the cancel event fires
   - After gather: iterate results in order, append tool_msg dicts to `history` and `messages`, aggregate media into `pending_media`
   - Return a ToolResult if cancelled, None otherwise

### Cancellation implementation

```python
# Sketch — not exact code
tasks = [asyncio.create_task(_execute_single_tool(call_ctx, tc, sem)) for ...]

async def _cancel_watcher():
    await cancel_event.wait()
    for t in tasks:
        t.cancel()

watcher = asyncio.create_task(_cancel_watcher()) if cancel_event else None
try:
    results = await asyncio.gather(*tasks, return_exceptions=True)
finally:
    if watcher:
        watcher.cancel()
```

### Tests

Add tests for `_execute_tool_calls`:
- Multiple tool calls run concurrently (mock tools with `asyncio.sleep(0.1)`, assert total time < 2× single sleep)
- Semaphore limits concurrency (set max=1, verify sequential behavior)
- One tool fails, others succeed — all results returned
- Cancellation kills in-flight tasks

Lint and run tests after. Existing tests should pass since single-tool-call behavior is unchanged.

---

## Phase 4: Update UI event handlers for tool_call_id

**Goal**: Mattermost and web UI track concurrent tools by `tool_call_id` instead of tool name.

**Files**: `mattermost.py`, `web/websocket.py`, `http_server.py`

### Prompt

Read these files for context:
- `src/decafclaw/mattermost.py` — focus on `ConversationDisplay.on_tool_start`, `on_tool_status`, `on_tool_end`, `on_confirm_request` (lines ~910-1040), and the event subscriber (lines ~620-650)
- `src/decafclaw/mattermost.py` — `_poll_confirmation` (line ~684) — publishes `tool_confirm_response` and needs to include `tool_call_id`
- `src/decafclaw/http_server.py` — `build_confirm_buttons` (line ~343) — builds button context and needs to include `tool_call_id`
- `src/decafclaw/web/websocket.py` — `on_turn_event` and tool event forwarding, plus `confirm_response` message handler

### Part 4a: Mattermost ConversationDisplay

1. Replace the single `_tool_post_id` field with a dict: `_tool_posts: dict[str, str] = {}` mapping `tool_call_id` → Mattermost post ID. Keep a `_first_tool_in_batch: bool` flag to track whether we've finalized text yet.

2. Update `on_tool_start(self, tool_name, args, tool_call_id="")`:
   - On the first tool_start in a batch (no entries in `_tool_posts` yet), finalize current text and optionally reuse the thinking placeholder
   - On subsequent concurrent tool_starts, always create a new post
   - Store post ID in `self._tool_posts[tool_call_id]`

3. Update `on_tool_status(self, tool_name, message, tool_call_id="")`:
   - Look up `self._tool_posts.get(tool_call_id)` to find the right post to edit. Fall back to editing the most recent post if `tool_call_id` not found (backward compat).

4. Update `on_tool_end(self, tool_name, result_text, display_text, media, tool_call_id="")`:
   - Look up and edit the right post by `tool_call_id`
   - Remove from `_tool_posts` when done
   - Handle media attachment per-post

5. Update the event subscriber (lines ~620-650) to pass `event.get("tool_call_id", "")` to all ConversationDisplay tool methods.

### Part 4b: Mattermost confirmation echo-back

1. **`_poll_confirmation`**: Add `tool_call_id` parameter. Include it in the `tool_confirm_response` event published by `_resolve`.

2. **`on_confirm_request`**: Accept `tool_call_id` parameter. Pass it through to `_poll_confirmation` and to `build_confirm_buttons`.

3. **`http_server.py` — `build_confirm_buttons`**: Add `tool_call_id` parameter. Include it in `base_context` and in the token registry `_make_token` calls. The HTTP callback handler that fires when a button is clicked must include `tool_call_id` in the `tool_confirm_response` event it publishes.

4. Update the event subscriber to pass `tool_call_id` to `on_confirm_request`.

### Part 4c: Web UI websocket

1. Update the `on_turn_event` tool event forwarding to include `tool_call_id` in all tool-related messages sent to the browser: `tool_start`, `tool_status`, `tool_end`, `confirm_request`.

2. Update the `confirm_response` message handler (the `elif msg_type == "confirm_response"` branch) to forward `tool_call_id` from the browser message to the event bus publish.

3. The frontend JavaScript changes are out of scope for this phase — the backend just needs to forward the field. The frontend can be updated separately.

Lint and run tests after.

---

## Phase 5: Simplify delegate tool

**Goal**: Replace `delegate` (nested array schema) with `delegate_task` (single string parameter). Child inherits parent's tools/skills.

**Files**: `tools/delegate.py`, `tools/__init__.py`

### Prompt

Read these files for context:
- `src/decafclaw/tools/delegate.py` — the current implementation
- `src/decafclaw/tools/__init__.py` — where delegate tools are registered (imports `DELEGATE_TOOLS`, `DELEGATE_TOOL_DEFINITIONS`)

Rewrite the delegate tool:

1. Rename `tool_delegate` to `tool_delegate_task`. Change signature to `async def tool_delegate_task(ctx, task: str) -> str`.

2. Simplify `_run_child_turn`:
   - Remove the `tools` parameter — child inherits all parent tools
   - Remove `system_prompt` parameter — always use default
   - Compute `allowed_tools` from the full tool registry (core TOOLS + parent's extra_tools) minus `delegate_task`, `activate_skill`, `refresh_skills`
   - Carry over `extra_tools`, `extra_tool_definitions`, `skill_data` from parent ctx
   - Keep `discovered_skills = []`, `on_stream_chunk = None`, timeout, child iterations

3. Update `DELEGATE_TOOLS` dict: key is now `"delegate_task"`, value is `tool_delegate_task`.

4. Update `DELEGATE_TOOL_DEFINITIONS` with the new flat schema:
   - Name: `delegate_task`
   - Single parameter: `task` (string, required)
   - Description: mention that for parallel work, call delegate_task multiple times in the same response

5. Remove all the multi-task batching logic (the `if len(tasks) == 1` branch, the gather, the task validation loop, the result formatting). `delegate_task` runs exactly one child turn and returns its result directly.

6. Update the excluded set in `_run_child_turn` from `"delegate"` to `"delegate_task"`.

7. Search the codebase for references to the old `"delegate"` tool name and update:
   - `tools/__init__.py` imports (should auto-resolve since we kept the dict/list names)
   - Any prompt files (AGENT.md, SOUL.md) that reference "delegate"
   - Any docs that reference the old schema
   - The `tool_status` publish in the current delegate code (remove it — single-task doesn't need a "delegating N tasks" status)

Lint and run tests after.

---

## Phase 6: Docs, cleanup, and issue closure

**Goal**: Update documentation, clean up any loose ends, close the issue.

**Files**: `CLAUDE.md`, `docs/`, `README.md`

### Prompt

1. Update `CLAUDE.md`:
   - Add convention: "Tool calls run concurrently when the model emits multiple in one response. `max_concurrent_tools` (default 5) caps parallelism."
   - Update the delegate tool description: `delegate_task` with single `task` parameter, child inherits parent tools/skills
   - Update key files list if `tools/delegate.py` changed significantly

2. Update `docs/` pages:
   - Update any docs that reference the `delegate` tool or its schema
   - Add a note about concurrent tool execution if there's an architecture doc

3. Update `README.md` tool table if delegate is listed there.

4. Add a comment to issue #71 summarizing what was done, then close it.

Lint, test, commit.

---

## Dependency Graph

```
Phase 1 (plumbing: tool_call_id in events)
  ↓
Phase 2 (config: max_concurrent_tools)
  ↓
Phase 3 (core: concurrent _execute_tool_calls)  ← the big one
  ↓
Phase 4 (UI: Mattermost + web tracking by ID)
  ↓
Phase 5 (delegate_task simplification)           ← independent of 3/4, but sequenced after for testing
  ↓
Phase 6 (docs + cleanup)
```

Phases 1-3 are the critical path. Phase 4 can be done in parallel with Phase 5 if needed. Phase 5 is independently valuable (fixes #71) and could be done first if we want a quick win, but the spec orders concurrent execution first since delegate_task's concurrency story depends on it.

## Testing Strategy

- **Phases 1-2**: Existing tests pass unchanged (no behavior change)
- **Phase 3**: Existing tests cover single-tool-call paths. Add tests:
  - Multiple mock tools with `asyncio.sleep` verify concurrent execution (total time < sum)
  - Semaphore limits concurrency (max=1 → sequential timing)
  - One tool fails, others succeed — all results returned in order
  - Cancel event kills all in-flight tasks
  - Media aggregated correctly from concurrent results
- **Phase 4**: Manual testing in Mattermost and web UI with multiple concurrent tool calls. Verify each tool gets its own progress message.
- **Phase 5**: Update any existing delegate tests. Test single-task delegation. Verify old `delegate` name is gone. Verify child inherits parent's extra_tools and skill_data.
- **Phase 6**: No new tests — doc review only.

## Risk Notes

- **Phase 3 ctx fork**: The `fork_for_tool_call` helper must copy all fields that tools or `execute_tool` depend on. If a new field is added to Context later and not included in the fork, concurrent tools will silently lose it. Consider: should `fork_for_tool_call` do a shallow copy of all fields instead of an explicit list? Trade-off: explicit is safer against accidental sharing, but fragile against new fields.
- **Phase 4 Mattermost race**: Multiple `on_tool_start` events arriving near-simultaneously could race on placeholder reuse and text finalization. Use the `_tool_posts` dict emptiness as the "first tool" signal, and guard text finalization with a flag.
- **Phase 4 confirmation buttons**: The HTTP button callback flow in `http_server.py` stores pending confirmations in a module-level `_token_registry`. Tokens must now include `tool_call_id` so responses route correctly. Verify the token creation and validation both handle the new field.
