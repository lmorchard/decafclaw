<!-- DO NOT EDIT — regenerate via 'make gen-message-types' -->
<!-- Source: src/decafclaw/web/message_types.json -->

# WebSocket Message Types

WebSocket message types exchanged between the decafclaw server (`src/decafclaw/web/websocket.py`) and the in-browser client. This page is generated from `src/decafclaw/web/message_types.json` — edit the manifest and run `make gen-message-types` to regenerate.

> **Field types are enforced.** The codegen at `scripts/gen_message_types.py` parses these field-type strings and emits matching TypedDicts (Python) and TypeScript interfaces (`tui/src/types.generated.ts`). Pyright validates every `ws_send` call site against the Python TypedDicts; tsc validates every TUI consumer against the TS interfaces. Drift between this manifest and either typed surface fails `make check-message-types`. Type-string grammar: `string`, `number`, `boolean`, `object`, `array of string`, `array of object`, `X | Y` unions; trailing `?` marks optional fields.

## Server → Client

### `background_event`

Background-task lifecycle event surfaced into a conversation timeline (e.g. delegated task started/finished).

**Fields:**

- `conv_id` — string
- `record` — object

### `canvas_update`

The conversation's canvas state changed; client should re-render the canvas panel.

**Fields:**

- `conv_id` — string
- `kind` — string
- `active_tab` — string | null
- `tab` — object | null
- `closed_tab_id` — string?

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
- `skill` — string?

### `compaction_done`

Conversation history compaction completed; client should reload history.

**Fields:**

- `conv_id` — string
- `before_messages` — number
- `after_messages` — number

### `confirm_request`

Server is asking the user to approve or deny a pending action (tool call, end-of-turn gate, widget input).

**Fields:**

- `conv_id` — string
- `confirmation_id` — string
- `action_type` — string
- `tool` — string
- `command` — string
- `suggested_pattern` — string
- `message` — string
- `approve_label` — string
- `deny_label` — string
- `tool_call_id` — string
- `action_data` — object

### `confirmation_response`

Replay of a prior confirmation response, forwarded to all tabs so non-originating tabs clear the widget.

**Fields:**

- `conv_id` — string
- `confirmation_id` — string
- `approved` — boolean
- `data` — object?

### `conv_history`

Page of historical messages for a conversation.

**Fields:**

- `conv_id` — string
- `messages` — array of object
- `has_more` — boolean
- `context_limit` — number
- `read_only` — boolean?
- `estimated_tokens` — number?
- `active_model` — string?
- `available_models` — array of string?
- `default_model` — string?
- `turn_active` — boolean?
- `pending_confirmation` — object?

### `conv_selected`

Confirmation that a select_conv subscribed this socket to the named conversation. May include initial conversation state.

**Fields:**

- `conv_id` — string
- `read_only` — boolean?
- `pending_confirmation` — object?

### `error`

Generic error surfaced to the client (bad request, unknown conversation, internal error).

**Fields:**

- `message` — string
- `conv_id` — string?

### `message_complete`

Final form of an assistant message after streaming completed (or when replayed from history).

**Fields:**

- `conv_id` — string
- `text` — string
- `role` — string?
- `final` — boolean?
- `usage` — object?
- `context_limit` — number?

### `model_changed`

The active model for a conversation changed (echoed back to all subscribers of that conversation).

**Fields:**

- `conv_id` — string
- `model` — string

### `models_available`

List of model identifiers the user can select in the UI.

**Fields:**

- `available_models` — array of string
- `default_model` — string

### `notification_created`

A new notification was added to the user's inbox (push from notification subsystem).

**Fields:**

- `record` — object
- `unread_count` — number

### `notification_read`

A notification was marked read (push from notification subsystem).

**Fields:**

- `ids` — array of string
- `unread_count` — number

### `reflection_result`

Output of the post-turn reflection step for a conversation.

**Fields:**

- `conv_id` — string
- `passed` — boolean
- `critique` — string
- `retry_number` — number
- `raw_response` — string
- `error` — string

### `tool_end`

Final result of a tool call. Replaces the in-flight tool_status with terminal state.

**Fields:**

- `conv_id` — string
- `tool` — string
- `tool_call_id` — string
- `result_text` — string
- `display_short_text` — string?
- `widget` — object?

### `tool_start`

Tool call has begun execution.

**Fields:**

- `conv_id` — string
- `tool` — string
- `tool_call_id` — string

### `tool_status`

Mid-flight progress update from a running tool.

**Fields:**

- `conv_id` — string
- `tool` — string
- `tool_call_id` — string
- `message` — string

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
- `text` — string

### `vault_changed`

A vault page or folder was created, edited, deleted, renamed, or moved. Clients showing vault content should re-fetch.

**Fields:**

- `path` — string
- `kind` — string

## Client → Server

### `cancel_turn`

Request cancellation of the conversation's in-flight agent turn.

**Fields:**

- `conv_id` — string

### `confirm_response`

User's decision on a pending confirm_request.

**Fields:**

- `conv_id` — string
- `confirmation_id` — string
- `approved` — boolean
- `always` — boolean
- `add_pattern` — boolean

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
- `confirmation_id` — string
- `data` — object
