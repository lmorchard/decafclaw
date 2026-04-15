# Confirmation Redesign & Conversation Manager

## Problem

Two related bugs (#235, #258) reveal a deeper architectural issue:

1. **#235 — Confirmations appear in wrong conversation.** The event bus is global and confirmation events aren't scoped per-conversation. Switching conversations in the web UI can show a confirmation from conversation A in conversation B.

2. **#258 — Confirmations lost on page reload.** Pending confirmations exist only in client-side memory (`ToolStatusStore.#pendingConfirms`) and a server-side `asyncio.Event`. Reloading the page loses the UI widget while the agent loop hangs waiting for a response that will never come.

Both stem from the same root cause: the agent loop is coupled to the WebSocket handler, and confirmations are ephemeral in-memory events with no persistence or conversation scoping.

## Solution

Extract a **conversation manager** as the central orchestrator. Agent loops become server-side tasks owned by the manager, independent of any transport. WebSocket, Mattermost, and interactive terminal become thin transport adapters. Confirmations become first-class messages in the conversation log.

## Architecture

### Conversation Identity

Conversations get a unique internal ID managed by the conversation manager, separate from any transport-specific identifier. Transports maintain their own mapping from channel-specific keys to conversation IDs:

- **Mattermost:** `root_id` (threads) or `channel_id` (top-level) → conversation ID
- **Web UI:** `web-{uuid}` slugs map 1:1 to conversation IDs (may be the same string)
- **Interactive terminal:** single implicit conversation per session

This decouples conversation identity from any particular transport. A conversation could theoretically be accessible from multiple channels (not in scope, but the model supports it).

### Conversation Manager

A server-side singleton that is the authority on conversation state. Owns:

- **Agent loop lifecycle** — start, suspend (at confirmation), resume, cancel, timeout
- **Conversation history** — load, append, archive
- **Confirmation state** — persist request to history, accept response, resume loop
- **Event stream** — uniform stream of events per conversation, fanned out to connected viewers
- **Conversation state** — replaces the scattered state currently in `mattermost.py` (`ConversationState`), `websocket.py` (`state` dict, `busy_convs`, `pending_msgs`), etc.
- **Message queuing** — when a turn is in progress (or suspended at confirmation), incoming messages are queued and drained when the turn completes. Replaces the `pending_msgs` dict currently in `websocket.py`.

High-level API for transports:

- `send_message(conv_id, text, attachments, ...)` — submit user input, starts/queues an agent turn
- `respond_to_confirmation(conv_id, confirmation_id, response)` — resolve a pending confirmation
- `cancel_turn(conv_id)` — cancel an in-progress agent turn
- `get_state(conv_id)` — get current conversation state including any pending confirmation
- `subscribe(conv_id, callback)` / `unsubscribe(conv_id, callback)` — attach/detach from a conversation's event stream
- `list_conversations(user_id)` — list conversations for a user
- `load_history(conv_id)` — load conversation history

### Transport Adapters

Thin adapters that handle connection management, input parsing, and output formatting. Each adapter:

- Parses incoming messages from its channel
- Calls the conversation manager's API
- Subscribes to the conversation's event stream
- Formats events for its channel (WebSocket JSON, Mattermost post edits, terminal stdout)
- Manages transport-specific UI (Mattermost emoji reactions, web UI components, terminal prompts)

Three adapters in scope:

1. **WebSocket adapter** — replaces current `websocket.py` handler. Multiple WebSocket connections can observe the same conversation. Connect/disconnect doesn't affect the agent loop.
2. **Mattermost adapter** — replaces current `mattermost.py` agent turn invocation. Keeps its display logic (post editing, progress formatting, emoji polling) but delegates agent loop and state management to the conversation manager.
3. **Interactive terminal adapter** — replaces current `interactive_terminal.py`. Simple REPL that calls the manager API.

### Confirmations as Conversation Messages

Confirmation requests and responses are persisted as messages in the conversation log:

```json
{"role": "confirmation_request", "confirmation_id": "abc123", "action_type": "run_shell_command", "action_data": {"command": "rm -rf /tmp/build"}, "message": "Allow shell command?", "approve_label": "Approve", "deny_label": "Deny", "tool_call_id": "tc_456", "timestamp": "..."}
```

```json
{"role": "confirmation_response", "confirmation_id": "abc123", "approved": true, "timestamp": "..."}
```

The agent loop suspends mechanically at a confirmation request. It resumes only when a matching response is appended to the history. This is a guardrail, not a signal for the LLM to interpret.

### Typed Confirmation Actions

Confirmations carry a fixed action type that determines what happens on approval. Each type has a known handler, making confirmations recoverable after server restart.

Initial action types:

- **`run_shell_command`** — execute a shell command. Data: `{command, suggested_pattern}`
- **`activate_skill`** — activate a named skill. Data: `{skill_name}`
- **`continue_turn`** — resume the agent loop (generic EndTurnConfirm). Data: `{}`
- **`advance_project_phase`** — project skill phase gate. Data: `{phase, project_id}`

New confirmation types require adding a handler to the registry — this is the right forcing function to ensure all confirmations are recoverable.

**Handler-controlled LLM context.** Confirmation handlers own what the LLM sees after a confirmation is resolved. Some handlers may inject specific prompts, tool results, or context based on the approval/denial. For example:

- Shell approval: on approve, execute the command and return the result as a tool result. On deny, inject a message telling the agent the command was denied.
- EndTurnConfirm: on approve, continue the loop. On deny, inject a prompt asking the agent what to change.
- Skill activation: on approve, activate and inject the skill context. On deny, tell the agent the skill was denied.

The confirmation response message in history records what happened, but the handler determines what goes into the LLM's next turn.

### Child Agent Confirmations

Child agents (spawned via `delegate_task`) can trigger confirmations. These must appear in the **parent** conversation, not in the child's isolated context. The current `event_context_id` mechanism routes child events to the parent — the conversation manager must preserve this:

- Child agent's confirmation request is persisted in the parent's conversation history
- The parent conversation's connected transports see and can respond to it
- The response routes back to the child agent's suspended loop via the manager

### Startup Recovery

On server startup, the conversation manager scans for conversations with interrupted confirmations:

1. Scan conversation archives for messages where the last entry is a `confirmation_request` with no matching `confirmation_response`
2. For each, register a pending confirmation in the manager's state
3. When a transport connects and the user views that conversation, they see the pending confirmation
4. When the user responds, the manager dispatches to the appropriate action handler based on the action type

### Event Stream

The conversation manager emits a uniform stream of events per conversation. Event types include (at minimum):

- `llm_start`, `llm_end` — LLM call boundaries
- `chunk` — streaming text chunk
- `message_complete` — finalized assistant message
- `tool_start`, `tool_end` — tool execution boundaries
- `tool_status` — progress updates from tools
- `confirmation_request` — a confirmation is pending
- `confirmation_response` — a confirmation was resolved
- `turn_complete` — agent turn finished
- `error` — something went wrong

Transports subscribe and format as appropriate. WebSocket forwards most events as JSON. Mattermost batches into post edits. Terminal prints text.

## Scope

### In scope

- Conversation manager as central orchestrator
- WebSocket, Mattermost, and interactive terminal as transport adapters
- Confirmations as persistent conversation messages with typed actions
- Startup recovery of interrupted confirmations
- Uniform event stream from manager to transports
- Multiple clients observing the same conversation
- Client disconnect/reconnect without losing state

### Out of scope

- Recovering agent turns interrupted mid-execution (not at a confirmation) after server restart
- New transport channels (Discord, Telegram, etc.) — but the adapter interface should make these straightforward to add later
- Removing sidecar metadata files (future work, noted by Les)
- Changes to the LLM client or tool execution internals

## Acceptance Criteria

1. Switching conversations in the web UI only shows confirmations for the active conversation (#235)
2. Reloading the page re-renders any pending confirmation for the active conversation (#258)
3. Multiple browser tabs viewing the same conversation both see confirmations and either can respond
4. Mattermost confirmations (shell, skill, EndTurnConfirm) work through the conversation manager
5. Interactive terminal confirmations work through the conversation manager
6. Server restart with a pending confirmation: on reconnect, the confirmation is re-rendered and functional
7. Agent loop runs independently of any transport connection — disconnecting all clients doesn't kill the turn
8. No regressions in existing functionality: streaming, tool execution, compaction, commands, etc.
