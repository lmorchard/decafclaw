# Architecture Overview

This is a narrative walkthrough for developers new to the DecafClaw codebase. It shows how the moving parts fit together — for reference material on specific subsystems, see the feature docs and [CLAUDE.md](../CLAUDE.md).

## The big picture

```
┌────────────────────────────────────────────────────────────────┐
│                      runner.py (process root)                  │
│                                                                │
│   ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│   │ Mattermost  │  │   HTTP +     │  │  Interactive        │  │
│   │ websocket   │  │   Web UI     │  │  Terminal           │  │
│   └──────┬──────┘  └──────┬───────┘  └──────────┬──────────┘  │
│          │                │                     │              │
│          └────────────────┼─────────────────────┘              │
│                           ↓                                    │
│                 run_agent_turn(ctx, message, history)          │
│                                                                │
│   ┌────────────────────────────────────────────────────────┐  │
│   │ 1. ContextComposer.compose()                           │  │
│   │    → system prompt + vault + history + tools           │  │
│   │ 2. call_llm(messages, tools)     ← loops              │  │
│   │ 3. if tool_calls: execute concurrently                 │  │
│   │ 4. append results, goto 2                              │  │
│   │ 5. if final text: reflection → deliver                 │  │
│   └────────────────────────────────────────────────────────┘  │
│                           │                                    │
│                           ↓                                    │
│   ┌────────────────────────────────────────────────────────┐  │
│   │ EventBus (events.py): tool_status, llm_start/end, …    │  │
│   │ Subscribers: Mattermost, terminal, web UI streams      │  │
│   └────────────────────────────────────────────────────────┘  │
│                                                                │
│   ┌────────────┐  ┌──────────────┐  ┌───────────────────┐    │
│   │ Heartbeat  │  │  Scheduled   │  │  MCP client       │    │
│   │ timer      │  │  task timer  │  │  (stdio + HTTP)   │    │
│   └────────────┘  └──────────────┘  └───────────────────┘    │
└────────────────────────────────────────────────────────────────┘
```

Everything runs in a single async process. Subsystems are parallel `asyncio` tasks coordinated by a shared `EventBus` and a forkable `Context`.

## Entry points and process topology

`runner.py` is the top-level orchestrator. On startup it:

1. Initializes MCP servers (shared across all subsystems)
2. Starts the HTTP server if `http.enabled` (web UI + Mattermost button callbacks)
3. Starts the Mattermost client if credentials are configured
4. Starts the heartbeat timer if an interval is set
5. Starts the scheduled task timer
6. Waits on a shutdown event

All of these run as independent `asyncio.Task`s. They share a single `app_ctx` with an `EventBus` for cross-component events.

Transports delegate turn lifecycle to the **ConversationManager** (`conversation_manager.py`), which handles context setup, history loading, confirmation persistence, message queuing, and per-conversation event streams. Transports are thin adapters — parse input, subscribe to the conversation's event stream, render output.

- **Mattermost transport** (`mattermost.py`) — websocket input, renders events back to Mattermost
- **Web UI WebSocket** (`web/websocket.py`) — renders events to the browser
- **Interactive terminal** (`interactive_terminal.py`) — stdin/stdout REPL
- **Heartbeat / scheduled tasks** — bypass the ConversationManager (fire-and-forget, no persistent state); call `run_agent_turn` directly with `task_mode="heartbeat"` or `"scheduled"`, skipping memory retrieval and reflection

## The forkable Context

The `Context` (`context.py`) is inspired by Go's context pattern. It carries request-scoped state through the call tree.

**Persistent fields:**
- `config` — resolved configuration
- `event_bus` — shared EventBus
- `context_id` — unique per fork, for correlation in logs

**Per-conversation identity:**
- `user_id`, `channel_id`, `thread_id`, `conv_id`

**Grouped state sub-objects:**
- `tokens` — per-turn token counters
- `tools` — activated tool definitions, deferred pool, pre-approvals, dynamic providers
- `skills` — which skills are activated in this conversation, skill-owned data
- `composer` — `ComposerState` tracking what went into the prompt and actual usage

**Per-turn flags:**
- `history`, `messages`, `cancelled` (Event for interruption)
- `is_child` (delegation), `task_mode` (heartbeat/scheduled), `skip_reflection`, `skip_vault_retrieval`

**Forking**

Three fork patterns:

```python
# Fork for a new conversation request (shares event bus, fresh state)
req_ctx = app_ctx.fork(conv_id="...", user_id="...", ...)

# Fork for a concurrent tool call (shares conversation state, gets own call_id)
call_ctx = parent.fork_for_tool_call(tool_call_id)

# Construct for a background task (heartbeat/scheduled — sensible defaults)
task_ctx = Context.for_task(config, event_bus, conv_id=..., task_mode="heartbeat")
```

Forking is explicit and cheap. Each fork gets a new `context_id` but keeps the parent's event bus, so events published by any descendant fan out to the same subscribers.

## The EventBus

The EventBus (`events.py`) is a simple in-process pub/sub. Subscribers are callbacks (sync or async) keyed by a subscription ID. `publish(event)` fans out to all subscribers, catching exceptions so one bad subscriber can't break the event.

**Global bus vs per-conversation streams.** The global EventBus is where tools, the agent loop, and subsystems publish raw events. The ConversationManager bridges these to **per-conversation event streams** — transports subscribe via `manager.subscribe(conv_id, callback)` and only receive events for their conversation.

**Why events?** They decouple tool execution from presentation. The agent loop publishes `tool_start`/`tool_end`/`tool_status`/`llm_start`/`llm_end`. Mattermost, the web UI, and the terminal each subscribe to their conversation's stream — they render progress in their own way without the agent knowing which transport is active.

**Event shapes:**
- `tool_start` / `tool_end` — published by the agent loop with `tool_call_id`, `tool_name`, `args`
- `tool_status` — published by tools during execution (progress updates)
- `llm_start` / `llm_end` — turn boundaries
- `llm_chunk` — streaming tokens (subscribed by the transport that's rendering)
- `tool_confirm_request` / `tool_confirm_response` — confirmation round-trips for shell/skill approvals
- `reflection_result` — self-reflection verdict (rendered per configured visibility)

Every event carries enough correlation info (`tool_call_id`, `context_id`) that subscribers can match it to an in-flight UI element.

## A single agent turn

`run_agent_turn(ctx, user_message, history)` is the loop. Here's what it does:

1. **Setup** — fork the context if needed, resolve the active model, apply task mode
2. **Compose context** — `ContextComposer.compose()` builds the full message array: system prompt, proactive vault retrieval, referenced pages, history, and classifies tools into active vs deferred. Returns a `ComposedContext`. See [Context Composer](context-composer.md).
3. **Iteration loop** — up to `max_tool_iterations` (default 200):
   - Build the per-iteration tool list (fetched tools may change mid-turn as the model calls `tool_search`)
   - Call the LLM via the provider abstraction — streaming tokens are published as events
   - If the response has text and no tool calls, break out of the loop
   - If the response has tool calls: execute them (see next section), append results to history, continue
4. **Reflection** — unless skipped, send the final response to a judge LLM. On failure, inject the critique and loop back. Up to `max_retries` times. See [Self-Reflection](reflection.md).
5. **Return** — deliver the final response text (and any accumulated media) to the transport

The history list is mutated in place throughout, so when the turn ends the archive already has everything appended.

## Tool execution concurrency

When the model emits multiple tool calls in a single response, they run concurrently:

```python
semaphore = asyncio.Semaphore(ctx.config.agent.max_concurrent_tools)  # default 5

tasks = [
    _execute_single_tool(parent.fork_for_tool_call(tc.id), tc, semaphore)
    for tc in tool_calls
]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

Each tool call gets a forked context with its own `current_tool_call_id`, sharing the parent's conversation state but bounded by the semaphore. Sync tools are dispatched via `asyncio.to_thread` (detected by `asyncio.iscoroutinefunction`).

This is how `delegate_task` achieves parallelism — the agent emits multiple `delegate_task` calls in one response and they each fork a child turn.

## Per-conversation isolation

Conversations are independent. A thread, a channel, and a web UI conversation each have their own `conv_id` and own history. The ConversationManager tracks busy flags and skill activations per `conv_id` — so one conversation running a long tool chain doesn't block another.

The rule: **one agent turn per conversation at a time, unlimited concurrent conversations.**

## Confirmations

When a tool needs user approval (shell commands, skill activation), the ConversationManager persists the confirmation request as a JSONL archive entry with `role: "confirmation_request"`. The agent loop suspends mechanically until it receives a matching `confirmation_response` entry. Pending confirmations survive page reload and server restart — a startup scan recovers them. See `confirmations.py` for the `ConfirmationAction` enum and handler registry.

## Transport adapters

Each transport is responsible for:

1. Receiving user input and handing it to the ConversationManager
2. Subscribing to the conversation's event stream for rendering
3. Delivering final responses and confirmation UI back to the user

The manager handles context setup, history loading, archive writes, and the agent loop itself. Transports don't know about each other and a new transport can be added by implementing those three steps.

## Persistence model

Everything is files on disk:
- **Conversation archives** — JSONL, append-only, one file per `conv_id` (includes messages, confirmations, model changes)
- **Vault** — markdown with YAML frontmatter, Obsidian-compatible
- **Embeddings** — SQLite + sqlite-vec
- **Checklists** — markdown checkboxes at `workspace/todos/{conv_id}.md`
- **Config** — JSON
- **Skills** — markdown (SKILL.md) + optional Python (`tools.py`)

This is crash-recoverable: if the process dies mid-turn, the archive still has everything up to that point, and the next message replays history from the archive. See [Data Layout](data-layout.md).

## Skills as tool providers

Skills (`skills.md`) are the extensibility surface. A skill directory contains:
- `SKILL.md` with frontmatter (name, description, `user-invocable`, `always-loaded`, etc.)
- Optional `tools.py` with `TOOLS`, `TOOL_DEFINITIONS`, and an optional `init()` / `get_tools()` hook

Skills load lazily — the catalog (names + descriptions) is in the system prompt, full content loads when the agent calls `activate_skill`. Skills with `always-loaded: true` (e.g., the vault skill) bypass this.

Dynamic skills export `get_tools(ctx)` to vary tools per turn based on state — this is how the `project` skill hides phase-inappropriate tools.

MCP servers (`mcp_client.py`) are similar in spirit: they're external tool providers registered on startup, with tools namespaced as `mcp__<server>__<tool>`.

## Dive deeper

- [Context Composer](context-composer.md) — how the prompt is assembled, vault retrieval, relevance scoring
- [Tool Search](tool-search.md) — how tool definitions are deferred behind search when over budget
- [Conversations](conversations.md) — archive format, compaction
- [Skills System](skills.md) — authoring skills, skill lifecycle
- [Sub-Agent Delegation](delegation.md) — how child agents work
- [Data Layout](data-layout.md) — everything on disk
- [Tools Reference](tools.md) — all built-in tools
