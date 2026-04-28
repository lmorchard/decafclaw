<!-- DO NOT EDIT — regenerate via 'make gen-message-types' -->
<!-- Source: src/decafclaw/web/message_types.json -->

# WebSocket Message Types

WebSocket message types exchanged between the decafclaw server (`src/decafclaw/web/websocket.py`) and the in-browser client. This page is generated from `src/decafclaw/web/message_types.json` — edit the manifest and run `make gen-message-types` to regenerate.

> **Future direction:** Field types are human-readable sketches today, not validators. Future work could grow them into typed entries (`{type, optional, enum}`, `{type: "array", items: ...}`) for runtime validation. Out of scope at present.

## Server → Client

### `background_event`

Background-task lifecycle event surfaced into a conversation timeline (e.g. delegated task started/finished).

**Fields:**

- `conv_id` — string
- `event` — object

### `canvas_update`

The conversation's canvas state changed; client should re-render the canvas panel.

**Fields:**

- `conv_id` — string
- `state` — object

### `chunk`

Streaming text fragment of an in-flight assistant message.

**Fields:**

- `conv_id` — string
- `text` — string

### `command_ack`

Acknowledgement that a slash-style user command was received and dispatched.

**Fields:**

- `conv_id` — string
- `command` — string

### `compaction_done`

Conversation history compaction completed; client should reload history.

**Fields:**

- `conv_id` — string

### `confirm_request`

Server is asking the user to approve or deny a pending action (tool call, end-of-turn gate, widget input).

**Fields:**

- `conv_id` — string
- `request_id` — string
- `kind` — string
- `payload` — object

### `confirmation_response`

Replay of a prior confirmation response, surfaced when reloading conversation history.

**Fields:**

- `conv_id` — string
- `request_id` — string
- `decision` — string

### `conv_history`

Page of historical messages for a conversation.

**Fields:**

- `conv_id` — string
- `messages` — array of object
- `before` — string | null

### `conv_selected`

Confirmation that a select_conv subscribed this socket to the named conversation. May include initial conversation state.

**Fields:**

- `conv_id` — string
- `model` — string | null

### `error`

Generic error surfaced to the client (bad request, unknown conversation, internal error).

**Fields:**

- `message` — string
- `conv_id` — string | null

### `message_complete`

Final form of an assistant message after streaming completed (or when replayed from history).

**Fields:**

- `conv_id` — string
- `message` — object

### `model_changed`

The active model for a conversation changed (echoed back to all subscribers of that conversation).

**Fields:**

- `conv_id` — string
- `model` — string

### `models_available`

List of model identifiers the user can select in the UI.

**Fields:**

- `models` — array of string

### `notification_created`

A new notification was added to the user's inbox (push from notification subsystem).

**Fields:**

- `notification` — object

### `notification_read`

A notification was marked read (push from notification subsystem).

**Fields:**

- `id` — string

### `reflection_result`

Output of the post-turn reflection step for a conversation.

**Fields:**

- `conv_id` — string
- `result` — object

### `tool_end`

Final result of a tool call. Replaces the in-flight tool_status with terminal state.

**Fields:**

- `conv_id` — string
- `tool_call_id` — string
- `name` — string
- `ok` — boolean
- `result` — string | object

### `tool_start`

Tool call has begun execution.

**Fields:**

- `conv_id` — string
- `tool_call_id` — string
- `name` — string
- `input` — object

### `tool_status`

Mid-flight progress update from a running tool.

**Fields:**

- `conv_id` — string
- `tool_call_id` — string
- `status` — string

### `turn_complete`

An agent turn finished (success, error, or cancellation).

**Fields:**

- `conv_id` — string

### `turn_start`

An agent turn has started; clients should clear any draft and show in-flight UI.

**Fields:**

- `conv_id` — string

### `user_message`

Echo of a user-authored message to all subscribers of the conversation (used for multi-tab sync).

**Fields:**

- `conv_id` — string
- `message` — object

## Client → Server

### `cancel_turn`

Request cancellation of the conversation's in-flight agent turn.

**Fields:**

- `conv_id` — string

### `confirm_response`

User's decision on a pending confirm_request.

**Fields:**

- `conv_id` — string
- `request_id` — string
- `decision` — string
- `extras` — object

### `load_history`

Request a page of historical messages for a conversation.

**Fields:**

- `conv_id` — string
- `limit` — number
- `before` — string | null

### `select_conv`

Subscribe this socket to a conversation's event stream.

**Fields:**

- `conv_id` — string

### `send`

Send a user message (and/or attachments) to the conversation.

**Fields:**

- `conv_id` — string
- `text` — string
- `attachments` — array of object

### `set_effort`

Deprecated backward-compat alias for set_model used by older web clients.

**Fields:**

- `conv_id` — string
- `model` — string

### `set_model`

Change the active model for a conversation.

**Fields:**

- `conv_id` — string
- `model` — string

### `widget_response`

Submission of an interactive widget input.

**Fields:**

- `conv_id` — string
- `request_id` — string
- `value` — object
