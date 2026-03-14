# Event Bus, Runtime Context, and Async Agent Loop

## Overview

Introduce an event-driven architecture to DecafClaw: a pub/sub event bus, a forkable runtime context, and an async agent loop. These primitives decouple tool execution from message delivery, enable real-time progress updates in Mattermost placeholders, and lay groundwork for concurrent subagents.

## Goals

- Tools can report progress without knowing about Mattermost or any specific output channel.
- The Mattermost placeholder message shows the latest tool status, then gets replaced by the final response.
- Interactive (terminal) mode also displays progress.
- The architecture supports future extensibility: multiple agents, multiple model configs, concurrent requests.

## Event Bus

A simple in-process pub/sub class:

```python
class EventBus:
    def publish(self, event: dict): ...
    def subscribe(self, callback) -> subscription_id: ...
    def unsubscribe(self, subscription_id): ...
```

- One shared bus per application.
- Events are structured dicts, always including `type` and `context_id`.
- Subscribers receive all events and filter by `context_id` (or event type) as needed.
- Subscriber callbacks can be sync or async. The bus awaits async callbacks.
- No rate limiting or debouncing for now — every publish triggers subscribers immediately.
- `publish` is async to support async subscribers.
- Subscribers must be cleaned up when a request completes (via `unsubscribe`).
- If a subscriber raises an exception, the bus catches and logs it — it must not kill the tool or agent loop.

## Context Object

A runtime context that holds both static config and request-scoped state. Inspired by Go's context pattern: start with a per-application context, fork/clone to per-request.

### App-level context

- `config` — the existing `Config` object (static settings)
- `event_bus` — the shared `EventBus` instance
- `context_id` — a unique ID for this context

### Request-level context (forked from app)

Inherits everything from the app context, plus:

- `context_id` — a new unique ID for this request

Forking creates a child context that shares the parent's event bus and config but gets its own identity. Mattermost-specific state (channel_id, placeholder_id, root_id) stays in the Mattermost layer — the subscriber closes over it, not the context.

```python
app_ctx = Context(config=config, event_bus=bus)
req_ctx = app_ctx.fork()

# Convenience publish — auto-includes context_id
await req_ctx.publish("tool_status", tool="tabstack_research", message="Searching...")
# Equivalent to:
await bus.publish({"type": "tool_status", "context_id": req_ctx.context_id, "tool": "tabstack_research", "message": "Searching..."})
```

The context should be designed so that eventually different forked contexts could carry different agent/model configurations.

## Event Types

All events include `type` and `context_id`. Additional fields vary by type.

### `tool_start`

Published by the agent loop when a tool is about to execute.

```python
{"type": "tool_start", "context_id": "...", "tool": "tabstack_research", "args": {"query": "..."}}
```

### `tool_status`

Published by tools during execution to report progress.

```python
{"type": "tool_status", "context_id": "...", "tool": "tabstack_research", "message": "Searching with 8 queries"}
```

### `tool_end`

Published by the agent loop when a tool finishes.

```python
{"type": "tool_end", "context_id": "...", "tool": "tabstack_research"}
```

### `llm_start` / `llm_end`

Published by the agent loop around LLM calls.

```python
{"type": "llm_start", "context_id": "...", "iteration": 1}
{"type": "llm_end", "context_id": "...", "iteration": 1}
```

## Async Agent Loop

The agent loop becomes async. This is the key architectural change that makes everything else work.

- `run_agent_turn` becomes `async def run_agent_turn(ctx, user_message, history)`.
- `call_llm` becomes async (or is wrapped with `asyncio.to_thread` if the HTTP client is sync).
- `execute_tool` becomes async. Blocking sync tools are wrapped with `asyncio.to_thread`.
- The event loop stays free during tool execution, so subscribers can `await` async operations (like `client.edit_message`).
- `run_interactive` becomes async, using `asyncio.run()` at the entry point. Blocking `input()` calls wrapped with `asyncio.to_thread`.
- Future benefit: concurrent subagents become natural (`asyncio.gather` or `create_task`).

## Tool Changes

- `execute_tool` becomes `async def execute_tool(ctx, name, arguments)`.
- All tool functions gain a `ctx` parameter.
- Tools are either **async** or **sync**. `execute_tool` inspects the function (e.g., `asyncio.iscoroutinefunction`) to decide how to call it:
  - **Async tools** — awaited directly. They can call `await ctx.publish(...)` for progress.
  - **Sync tools** — wrapped with `await asyncio.to_thread(fn, ...)`. They don't publish status; the agent loop's `tool_start` event announces them.
- Async tools: `tabstack_research`, `tabstack_automate` (streaming, publish `tool_status` per SSE event). The Tabstack SDK provides `AsyncTabstack` with `AsyncStream` supporting `async for` iteration on both methods.
- Sync tools: `shell`, `read_file`, `web_fetch`, `tabstack_extract_markdown`, `tabstack_extract_json`, `tabstack_generate`.
- Tabstack initialization switches from `Tabstack` to `AsyncTabstack` for the async tools. Non-streaming Tabstack tools can also use the async client or remain sync — implementation decision.
- The tool registry (`TOOLS` dict) holds a mix of sync and async functions — `execute_tool` handles both transparently.

## Agent Loop Changes

- `run_agent_turn` receives the context (which carries config) instead of config directly.
- Publishes `llm_start`/`llm_end` around each LLM call.
- Publishes `tool_start`/`tool_end` around each tool execution.
- Passes context through to `execute_tool`.

## Subscribers

### Mattermost subscriber

- Created per-request inside `on_message`, closing over its own `placeholder_id`, `channel_id`, and `root_id`.
- Uses `context_id` solely as a filter to match events to the correct request.
- Events carry no Mattermost routing info (no placeholder_id, channel_id, etc.) — that's Mattermost's domain, not the tools'.
- On `tool_start` or `tool_status` events: edits the placeholder message with the status text.
- Default status text for `tool_start`: tool name (e.g., "Running shell...").
- For `tool_status`: uses the event's `message` field directly.
- On `llm_start`: edits placeholder back to "Thinking..." (between tool iterations when LLM is reasoning again).
- When the agent turn completes, the placeholder is replaced with the final response (existing behavior).

### Terminal subscriber

- Subscribes to the event bus in `run_interactive`.
- Prints progress to stdout (e.g., status line or simple print).

## Placeholder Behavior

- Replace the placeholder content with the latest status on each event — no accumulation.
- Whichever tool is currently running, its status is what the user sees.
- When the final response is ready, replace the placeholder entirely (existing behavior, unchanged).

## Scope

### In scope

- EventBus class
- Context class with fork support
- Wire context through agent loop and tools
- Tabstack streaming tools publish `tool_status` events from SSE
- Mattermost subscriber edits placeholder on progress events
- Terminal subscriber prints progress
- Agent loop publishes `llm_start`/`llm_end` and `tool_start`/`tool_end`

### Out of scope (future)

- Rate limiting / debouncing of placeholder edits
- Multiple agents / model configs per context (design for it, don't build it)
- Persistent event log
- SSE events fed into LLM prompt
