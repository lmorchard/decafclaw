# Specification — 750-web-ui-state-resync-on-reconnect

## Goal
Implement a full-refetch design on WebSocket reconnect in the Web UI to reconcile all states (messages, canvas, and sticky) that may have changed during an outage, preventing silent staleness of the client.

## Requirements
1. Clear and refetch conversation messages/history on WebSocket reconnection.
2. Automatically refetch canvas state for the active conversation when the WebSocket reconnects.
3. Automatically refetch sticky state for the active conversation when the WebSocket reconnects.

## Design Decisions
- **`conversation-store.js`:** Clear `MessageStore` and `ToolStatusStore` and re-send `LOAD_HISTORY` inside the `#resubscribe()` reconnection handler.
- **`canvas-state.js` & `sticky-state.js`:** Register a window-level listener for the `ws-connected` event (emitted when the WebSocket reopens), and automatically call `setActiveConv(_state.active)` to refetch and republish their latest state via REST.
- **`conversation-store.test.js`:** Update the existing reconnection test (`keeps already-loaded messages when the socket reopens`) to expect the full refetch behavior instead of detaching history reloading.

## What we are NOT doing
- We are not implementing advanced since-cursors or delta synchronization. Full refetch is selected as the designated simple and robust recovery strategy.
