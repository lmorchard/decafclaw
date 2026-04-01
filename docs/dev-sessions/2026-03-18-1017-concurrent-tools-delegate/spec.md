# Concurrent Tool Calls & Delegate Simplification — Spec

## Status: Ready

## Background

The agent loop currently executes tool calls sequentially, even when the model emits multiple calls in a single response — a signal that they're independent. This wastes time on I/O-bound tools (vault reads, web fetches, memory searches).

Separately, the `delegate` tool has a nested array-of-objects schema that causes Gemini 2.5 Flash to produce `malformed_function_call` errors (see [#71](https://github.com/lmorchard/decafclaw/issues/71)). The nested schema is unnecessary if the agent loop itself handles concurrency.

## Goals

1. Run tool calls concurrently when the model emits multiple calls in one response.
2. Simplify the delegate tool to a flat, single-task schema that any model can generate.
3. Keep the UI coherent when multiple tools are in flight simultaneously.

## Part 1: Concurrent Tool Execution

### Behavior

When the model returns multiple tool calls in a single response, execute them concurrently via `asyncio.gather`. No safe-list or opt-in mechanism — all tools are treated as concurrent-safe by default. If concurrency causes problems with specific tools, we'll revise their descriptions or add guards later.

### Concurrency Limit

Add a configurable maximum number of concurrent tool calls (`max_concurrent_tools` in config). Use an `asyncio.Semaphore` to cap how many tool calls run at once. This prevents resource exhaustion when the model emits many calls (e.g. 10+ delegate_task or web fetch calls). Default TBD — something like 5-10.

### tool_call_id Everywhere

Add `tool_call_id` to **all** tool-related events so that every piece of a tool call's lifecycle can be tracked and reassembled:

- `tool_start` events
- `tool_status` events (mid-execution progress updates)
- `tool_end` events
- `tool_confirm_request` and `tool_confirm_response` events
- Archive entries (tool result messages already have `tool_call_id` via the model response)

This is the primary mechanism for tracking concurrent calls. Events will naturally interleave (e.g. `tool_start(A)` → `tool_start(B)` → `tool_status(B)` → `tool_end(B)` → `tool_end(A)`), and consumers use `tool_call_id` to correlate them.

### tool_call_id Threading Into Tools

`execute_tool` currently doesn't receive `tool_call_id`. However, some tools publish their own `tool_status` events mid-execution (tabstack, claude_code, delegate). These status events need `tool_call_id` to be correlatable with the right tool call in the UI.

Fix: set `ctx.current_tool_call_id` before calling `execute_tool` (or pass it as a field on a per-call context fork). Tools that publish `tool_status` via `ctx.publish` will include it automatically if the publish helper adds it from the context. This avoids changing every tool's signature — the ID flows through `ctx`.

### Confirmation

`request_confirmation` currently matches responses by `context_id` + `tool_name`. This breaks when two calls to the same tool (e.g. two `shell` commands) run concurrently — both would match the same response.

Fix: add `tool_call_id` to both `tool_confirm_request` and `tool_confirm_response` events, and match on `tool_call_id` instead of (or in addition to) `tool_name`. This requires threading `tool_call_id` through to `request_confirmation` and updating the Mattermost and web UI confirmation handlers to echo it back.

### UI Changes

- **Mattermost**: `ConversationDisplay` currently tracks tool state by tool name (`on_tool_start`, `on_tool_status`, `on_tool_end`). Needs to be refactored to track by `tool_call_id` so each concurrent call gets its own progress placeholder, updated independently. Multiple tool progress messages may be visible simultaneously.
- **Web UI**: Same — the websocket event forwarding and frontend tool status tracking need to key on `tool_call_id` instead of tool name.

### Result Ordering

`asyncio.gather` preserves input order in its return values. Tool results are archived in completion order (as they finish), but the history assembled for the model preserves the original call order (matched by `tool_call_id`).

### Cancellation

When the cancel event fires, all in-flight concurrent tool tasks are cancelled immediately. This is an emergency stop — no graceful completion of remaining tasks.

### Error Handling

If one tool call fails, the others continue running. Failed calls return an error `ToolResult` as they do today. The model receives all results (successes and failures) and decides how to proceed.

### Media Collection

Each concurrent tool task collects its own media. After `asyncio.gather` completes, media is aggregated from all results in call order and appended to `pending_media`. No shared mutable list during concurrent execution.

### Archiving

Tool result messages are archived as each concurrent call completes (completion order). Each archive entry carries `tool_call_id`, so the correct order can be reconstructed if needed. The history assembled for the model uses call order (preserved by `asyncio.gather`).

### Implementation Target

`_execute_tool_calls` in `agent.py` — replace the sequential `for` loop with `asyncio.gather` gated by an `asyncio.Semaphore(max_concurrent_tools)`. Each tool call becomes a coroutine that:
1. Sets `ctx.current_tool_call_id` (or uses a per-call ctx fork)
2. Acquires the semaphore
3. Publishes `tool_start` (with `tool_call_id`)
4. Calls `execute_tool`
5. Publishes `tool_end` (with `tool_call_id`)
6. Archives its tool result message
7. Returns `(tool_result_msg, media_list)`

After gather completes:
- Append all result messages to history/messages in original call order
- Aggregate media from all results into `pending_media`

`ctx.publish` should automatically include `tool_call_id` from `ctx.current_tool_call_id` when present, so tools that emit `tool_status` events don't need code changes.

**Config addition**: `max_concurrent_tools` (int, default 5) in `config.py`.

## Part 2: Delegate Tool Simplification

### Current Problem

The `delegate` tool schema uses a nested array of objects:
```
tasks: array → items: object → { task: string, tools: array of strings, system_prompt: string }
```
This causes Gemini to produce `malformed_function_call` with 0 completion tokens.

### New Design

Rename `delegate` to `delegate_task`. Accept a single flat parameter:

| Parameter | Type   | Required | Description |
|-----------|--------|----------|-------------|
| `task`    | string | yes      | Task description — becomes the child agent's input |

That's it. No `tools`, no `system_prompt`, no nested objects.

- **Tools**: The child agent inherits all of the parent's available tools and activated skills, minus `delegate_task` itself (to prevent recursion).
- **System prompt**: Uses the default child system prompt. Removed as a parameter — can be re-added if a real need surfaces.
- **Concurrency**: When the model wants to run multiple subtasks in parallel, it emits multiple `delegate_task` calls in one response. Part 1 (concurrent tool execution) handles the parallelism.

### Schema

```json
{
  "type": "function",
  "function": {
    "name": "delegate_task",
    "description": "Delegate a subtask to a child agent. The child runs as an independent agent turn with access to the same tools and skills. Use when a request has an independent part that can be handled separately. For parallel work, call delegate_task multiple times in the same response.",
    "parameters": {
      "type": "object",
      "properties": {
        "task": {
          "type": "string",
          "description": "Task description with enough context for the child agent to work independently"
        }
      },
      "required": ["task"]
    }
  }
}
```

### Child Agent Behavior

- Inherits parent's `extra_tools`, `extra_tool_definitions`, and `skill_data`
- `allowed_tools` excludes `delegate_task`, `activate_skill`, `refresh_skills`
- `discovered_skills` cleared (children don't discover/activate new skills)
- Uses `child_max_tool_iterations` from config
- Timeout via `child_timeout_sec` from config
- No streaming (child results returned as text to parent)

## Out of Scope

- Tool-level concurrency guards or safe-lists (handle reactively if needed)
- Streaming from child agents to the UI
- Nested delegation (child calling delegate_task)
