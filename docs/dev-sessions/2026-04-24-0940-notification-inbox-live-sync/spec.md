# WebSocket push for notification inbox

Tracking issue: #332

## Problem

The web UI notification bell polls `/api/notifications/unread-count` every 30s.
Two problems fall out of that:

1. **Noise.** Most polls return an unchanged count. Every open browser tab hits
   the endpoint on the same schedule, multiplied per tab.
2. **Multi-tab inconsistency.** Read-state changes don't propagate. Tab A
   marking a notification read leaves tab B's bell stale until its next poll,
   and the click in B can race into a 404.

## Goal

Push notification events over the existing per-user WebSocket so the bell
updates on change, not on interval. Covers both "stop polling" and
"multi-tab sync" in one change since both problems have the same solution.

## Architecture

### Event bus

Two events flow over the existing in-process `EventBus`:

- `notification_created` — already published by `notify()` in
  `src/decafclaw/notifications.py`. Carries `{type, record}` today; this
  session **extends the payload** with `unread_count` (computed once at
  publish time, after the inbox write). Additive change — existing channel
  adapter subscribers (Mattermost DM, email, vault page) simply ignore the
  new field.
- `notification_read` — **new**. Published from inside
  `notifications.mark_read()` and `notifications.mark_all_read()` when given
  an optional `event_bus` argument (mirrors the `notify(..., event_bus=...)`
  signature). Payload: `{type: "notification_read", ids: [...],
  unread_count: int}`. One message per REST call regardless of how many
  items were marked (`read-all` sends one event with many ids).

  For `mark_all_read`, the publish enumerates the set of ids that were
  **previously unread** (snapshotted before the persist step) and emits
  them as `ids`. This lets the frontend update individual list items
  without re-fetching. If the snapshot is empty (nothing was unread), no
  event is published — the operation is a no-op, emitting noise would
  just churn subscribers.

**Count ownership.** `unread_count` is computed **once at publish time** by
the function writing the state change (inside `notify()`, `mark_read()`,
`mark_all_read()`), and carried in the event payload. WebSocket subscribers
forward the field as-is — they do NOT recompute it on receipt. This keeps
notification-list I/O out of the fan-out path: N connected tabs means N
socket sends, not N file reads.

**Ordering.** Persist the state change first, then publish. A subscriber
(channel adapter or WS bridge) that reacts to the event always sees the
corresponding inbox state on disk.

Rationale for publishing from inside `notifications.py` (not from the REST
handler): read-state is a notification-layer concern. Any future code path
that marks things read — tool, another transport, periodic sweep — gets
broadcast for free. Matches `notify()`.

### WebSocket bridge

In the existing `websocket_chat` handler (`src/decafclaw/web/websocket.py`),
register a single event bus subscriber for the lifetime of the connection.
The bus dispatches every event to every subscriber, so the callback filters
by `event["type"]` and ignores anything that isn't a notification event.
The subscription ID returned by `event_bus.subscribe()` is captured and
passed to `event_bus.unsubscribe()` in the connection's cleanup block.

Forward matching events to the socket as typed JSON messages:

- `{type: "notification_created", record: {...}, unread_count: N}`
- `{type: "notification_read", ids: [...], unread_count: N}`

`unread_count` is already on the event bus payload (see count ownership
above) so the forwarder is a pure pass-through — no extra I/O per
subscriber.

No per-user filtering. Every connected WebSocket forwards every event.
Multi-tab sync falls out naturally (each tab has its own handler subscribing
independently). Multi-user routing is deliberately deferred — tracked in
#336.

Subscriptions unregister on disconnect.

### REST endpoints

Kept, not removed:

- `GET /api/notifications` — list. Still hit on dropdown-open. Unchanged.
- `GET /api/notifications/unread-count` — seed. Hit on component mount and
  on every WebSocket reconnect. **No longer hit on a 30s interval.**
- `POST /api/notifications/{id}/read` — unchanged behavior; now publishes
  the `notification_read` event via the route handler's `event_bus`.
- `POST /api/notifications/read-all` — same.

### Frontend

`src/decafclaw/web/static/components/notification-inbox.js`:

- Drop the `setInterval(#refreshCount, POLL_INTERVAL_MS)` and the
  `_pollTimer` field.
- Follow the existing pattern for reacting to WebSocket traffic (see
  `turn_complete` in `app.js:384`): `app.js` owns the single `ws` instance,
  filters inbound messages, and re-dispatches them as window-level
  `CustomEvent`s. The bell listens for those window events, not for the raw
  socket messages. This keeps `ws` module-local to `app.js` and avoids
  leaking it to every component.
- `app.js` new bridges:
  - `msg.type === "notification_created"` → `window.CustomEvent("notification-created", {detail: msg})`
  - `msg.type === "notification_read"` → `window.CustomEvent("notification-read", {detail: msg})`
  - `ws "open"` → `window.CustomEvent("ws-connected")`
- On mount: fire `#refreshCount()` once (seed). Also fire it on every
  `ws-connected` window event (covers initial connect + every reconnect —
  `WebSocketClient` already handles auto-reconnect backoff).
- On `notification-created`: update `_count` from the message's
  `unread_count`; if the dropdown is currently open, re-fetch the list so
  the new record appears.
- On `notification-read`: update `_count`; if the dropdown is currently
  open, re-fetch the list (or apply the delta in place — implementation
  detail).

## Acceptance criteria

- Network-tab shows no `/api/notifications/unread-count` request 30s after
  page load on an idle inbox.
- Firing a notification (agent `send_notification` tool, scheduled task,
  etc.) updates the bell in ≤1s with no REST round-trip.
- Opening two tabs, marking an item read in tab A → tab B's count
  decrements and the item appears read in B's dropdown without refreshing
  the page.
- "Mark all read" from tab A clears the badge in tab B in ≤1s.
- Temporarily cutting the WebSocket (server restart, network blip) and
  letting `WebSocketClient` auto-reconnect restores the correct count on
  reconnect without a page reload.
- Existing REST endpoints still return valid responses for non-WS clients
  (tests, curl).

## Out of scope

- **Full-list push over the WebSocket.** Only the unread count + new record
  get pushed. The full-list endpoint stays REST (dropdown-open fetch).
- **Clear-all / delete operations.** Not a current feature.
- **Offline replay buffer.** A tab disconnected during a `notification_read`
  stays stale until it reconnects; reconnect seed restores the count; the
  list catches up on next dropdown-open. No server-side replay queue.
- **Multi-user routing** (recipient filtering, per-user inbox). Tracked in
  #336.
- **Rate limiting of pushes** (flood protection when a producer spams
  notifications). Tracked in #334.

## Testing

- **Server-side unit:**
  - `notifications.mark_read(id, event_bus=bus)` publishes exactly one
    `notification_read` event with the correct `ids` + `unread_count`.
  - `notifications.mark_all_read(event_bus=bus)` publishes one event with
    all previously-unread ids.
  - Back-compat: calling either function without `event_bus=` works
    (pure state change, no publish).
- **Web gateway integration:**
  - WS handler subscribes to both events on connect; unsubscribes on
    disconnect (no leaked subscribers across a connect/disconnect cycle).
  - Publishing `notification_created` on the bus results in the forwarded
    JSON message arriving on an open socket, with `unread_count` populated.
  - Same for `notification_read`.
- **Frontend:** manual verification covers the acceptance criteria above
  (two-tab mark-read, bell update without poll, reconnect reseeding).
  Existing automated frontend tests are limited; this session doesn't add
  a JS test harness.
