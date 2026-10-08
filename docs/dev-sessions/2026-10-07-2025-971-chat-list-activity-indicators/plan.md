# Plan: Activity Indicators on Chat List Rows in Sidebar (#971)

## 1. Backend Subsystems & WebSocket Wire Types
- Add `conversation_status` message type to `src/decafclaw/web/message_types.json` with fields `{"conv_id": "string", "status": "string"}`.
- Run `make gen-message-types` to update `src/decafclaw/web/message_types.py`, `src/decafclaw/web/static/lib/message-types.js`, `docs/websocket-messages.md`, and `tui/src/types.generated.ts`.
- In `src/decafclaw/conversation_manager.py`:
  - Add `get_activity_status(conv_id: str) -> str`:
    - Returns `"waiting"` if `state.pending_confirmation is not None`
    - Returns `"busy"` if `state.busy`
    - Returns `"idle"` otherwise
  - Publish `conversation_status` events on `self.event_bus`:
    - On turn start in `_start_turn` -> `"busy"`
    - On `request_confirmation` and `post_confirmation` -> `"waiting"`
    - On `respond_to_confirmation` / `cancel_pending_confirmation` -> `"busy"` if `state.busy` else `"idle"`
    - In `run()` finally block in `_start_turn` -> `"finished"`
    - In `cancel_turn` -> `"idle"`
- In `src/decafclaw/web/websocket.py`:
  - Add `_make_conversation_status_forwarder(ws_send)` subscribing to `event_bus` to broadcast `conversation_status` events to all connected clients.
- In `src/decafclaw/http_server.py`:
  - Add `status: str = "idle"` to `ConversationListingItem` and `SystemConversationListingItem`.
  - In `list_conversations`, `list_archived_conversations`, and `list_system_conversations`: query `manager.get_activity_status(conv_id)` if manager is available.
- Run `make gen-api-client` to update the TypeScript client in `lib/api-client/`.

## 2. Frontend Store & Component Logic
- In `src/decafclaw/web/static/lib/conversation-store.js`:
  - Maintain `#conversationStatuses = new Map()`.
  - Add `getConversationStatus(convId): string` method.
  - In `listConversations`, populate `#conversationStatuses` from API items (defaulting to item.status if not already 'finished').
  - In `#handleMessage`:
    - Handle `MESSAGE_TYPES.CONVERSATION_STATUS`:
      - If `status === 'finished'`:
        - If `conv_id === this.#currentConvId`: set to `'idle'`
        - Else: set to `'finished'`
      - Else: set to `msg.status`
      - Emit store change.
  - In `selectConversation(convId)`:
    - If `#conversationStatuses.get(convId) === 'finished'`, reset to `'idle'`.
- In `src/decafclaw/web/static/components/conversation-sidebar.js`:
  - In `#renderConversationItem(conv, opts)`:
    - Query `status = this.store?.getConversationStatus(conv.conv_id) || conv.status || 'idle'`.
    - If `status !== 'idle'`, render a `<span class="conv-status ${status}" title="..." aria-label="...">...</span>`.
- In `src/decafclaw/web/static/styles/sidebar.css`:
  - Add styles for `.conv-status`, `.conv-status.busy`, `.conv-status.waiting`, `.conv-status.finished`.
  - Adjust styling when row is active or hovered.

## 3. Tests & Verification
- Add backend tests in `tests/test_conversation_manager.py` and `tests/test_http_server.py`.
- Add frontend tests in `conversation-store.test.js`, `conversation-sidebar.test.js`, and verify `sidebar-row-a11y.test.js`.
- Run `make test`, `make test-js`, `make check-js`, and `make lint`.
