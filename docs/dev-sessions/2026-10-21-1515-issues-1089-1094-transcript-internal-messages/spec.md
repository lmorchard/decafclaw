# Spec: Issues 1089 & 1094 — Transcript Internal Messages & Compaction Events

## Problem Summary

1. **Issue 1089**: Auto-injected context records (`conversation_notes`, `recent_journal`, `workspace_references`, `mcp_references`) and lifecycle records (`cancel_marker`, `turn_aborted`) in `archive.jsonl` are displayed upon history reload as large, misleading user message bubbles because `chat-message.js` falls through to `<user-message>`.
2. **Issue 1094**: Compaction events (`compaction_end` / `WSMessageType.COMPACTION_DONE`) are only held ephemerally in-memory on the client and never persisted to `archive.jsonl`. When switching conversations or refreshing the browser, compaction notices disappear from the transcript. Furthermore, the transient notice displays minimal detail (`N → M messages`) rather than token reduction metrics and elapsed duration matching `compaction_display.py`.

## Detailed Specifications

### 1. Compaction Persistence & LLM Filter (Issue 1094)
- In `src/decafclaw/compaction.py:compact_history()`, in the `finally:` block:
  - Format `notice_text` using `compaction_notice()` from `decafclaw.compaction_display`.
  - Append a record with `role: "compaction"` containing `content: notice_text` and `data` (before/after messages, estimated tokens before/after, elapsed_sec, success) to `archive.jsonl` using `append_message(config, conv_id, compaction_record)`.
  - Wrap archive persistence in `try/except` to maintain fail-open behavior.
- In `src/decafclaw/compaction.py`:
  - `_split_into_turns()`: Ignore/skip messages with `role in ("compaction", "model", "mode")` when grouping archive messages into conversation turns.
  - `flatten_messages()`: Skip messages with `role in ("compaction", "model", "mode")` so metadata records are omitted from LLM prompts during summarization.
- In `src/decafclaw/context_composer.py`:
  - Verify `role: "compaction"` is excluded from LLM prompt context construction and token accounting (`countable_roles = LLM_ROLES | ROLE_REMAP.keys()`).

### 2. WebSocket & Live Compaction Events (Issue 1094)
- In `src/decafclaw/web/message_types.json`:
  - Add optional fields `elapsed_sec: "number?"`, `estimated_tokens_before: "number?"`, `estimated_tokens_after: "number?"` to `compaction_done`.
  - Run `make gen-message-types`.
- In `src/decafclaw/web/websocket.py`:
  - Populate these fields on `WSMessageType.COMPACTION_DONE` from the `compaction_end` event.
- In `src/decafclaw/web/static/lib/message-store.js`:
  - Format live `MESSAGE_TYPES.COMPACTION_DONE` with elapsed time, message delta, and token reduction metrics matching `compaction_display.py`.
  - Store `data` on the live compaction message.

### 3. Frontend Message Rendering (Issues 1089 & 1094)
- In `src/decafclaw/web/static/components/chat-message.js`:
  - Add dedicated branches for internal context roles:
    - `conversation_notes`: render `<tool-message .tool=${'conversation_notes'} .content=${this.content} .icon=${'📝'}></tool-message>`
    - `recent_journal`: render `<tool-message .tool=${'recent_journal'} .content=${this.content} .icon=${'📓'}></tool-message>`
    - `workspace_references`: render `<tool-message .tool=${'workspace_references'} .content=${this.content} .icon=${'📁'}></tool-message>`
    - `mcp_references`: render `<tool-message .tool=${'mcp_references'} .content=${this.content} .icon=${'🔌'}></tool-message>`
  - Add dedicated branches for lifecycle events:
    - `cancel_marker`: render `<div class="chat-event cancel-marker"><span class="icon">🛑</span><span class="title">Turn cancelled</span>${this.content ? html`<span class="detail">${this.content}</span>` : ''}</div>`
    - `turn_aborted`: render `<div class="chat-event turn-aborted"><span class="icon">⚠️</span><span class="title">Turn aborted</span>${this.content ? html`<span class="detail">${this.content}</span>` : ''}</div>`
  - Enhance `compaction` branch:
    - Render `<div class="compaction-notice ...">` with icon (`📦`), summary text, and if metrics are present in `this.record` or `this.data`, display metrics badge (message delta, token reduction percentage, elapsed seconds).
- In `src/decafclaw/web/static/components/chat-view.js`:
  - Ensure `.record=${m.record || m.data || null}` is passed to `<chat-message>`.
- In `src/decafclaw/web/static/styles/chat.css`:
  - Add styling for `.chat-event.cancel-marker`, `.chat-event.turn-aborted`, and `.compaction-notice`.

### 4. History Replay & Merging (Issue 1089)
- In `src/decafclaw/web/static/lib/message-store.js`:
  - In `MESSAGE_TYPES.CONV_HISTORY`: Map `compaction` records carrying `data` to include `record: m.data || m.record`.
  - In `#mergeToolMessages`: Ensure tool calls and responses merge properly, and non-merged synthetic messages are preserved without regression.

### 5. Automated Tests
- Python tests:
  - `tests/test_compaction.py`: Verify successful and failed compaction events are written to `archive.jsonl`, and that `role: "compaction"` is excluded from turns and flattened LLM inputs.
  - `tests/test_context_composer.py`: Verify `role: "compaction"` messages in history are omitted from prompt assembly and token accounting.
- JavaScript tests:
  - `src/decafclaw/web/static/components/chat-message.test.js`: Verify proper rendering elements for `conversation_notes`, `recent_journal`, `workspace_references`, `mcp_references`, `cancel_marker`, `turn_aborted`, and `compaction`.
  - `src/decafclaw/web/static/lib/message-store.test.js`: Verify history replay with compaction events and internal context messages.
