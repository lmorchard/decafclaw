# Plan: WebSocket push for notification inbox

Refer to `spec.md` for full behavior and architecture.

Work breaks into five ordered phases. Each lands a commit with lint + tests
green. Phases are sized to be independently reviewable; the last commit of
the branch also carries docs updates.

---

## Phase 1 — Extend `notify()` payload with `unread_count`

**What:** Add `unread_count` to the existing `notification_created` event
payload published by `notifications.notify()`. Purely additive — existing
channel adapter subscribers (Mattermost DM, email, vault page) ignore the
new field.

**Files:**
- `src/decafclaw/notifications.py` — compute `unread_count(config)` after
  the inbox write, before the event-bus publish; include it in the event
  dict.
- `tests/test_notifications.py` — assert the field is present on the
  published event, and that it matches `unread_count(config)`.

**Verify:**
- `make lint && make test` green.
- Existing channel adapter tests still pass (they only assert on `type` and
  `record`).

**After this phase:** `notification_created` events carry `unread_count`.
Nothing consumes it yet.

### Prompt

> Update `src/decafclaw/notifications.py::notify` so the
> `notification_created` event bus payload includes the current
> `unread_count` computed immediately after the inbox append. Persist-
> then-publish ordering must stay intact — the count reflects post-append
> state. Add a unit test in `tests/test_notifications.py` that calls
> `notify()` with a fake event bus, captures the published event, and
> asserts `unread_count` equals `notifications.unread_count(config)`.
> Verify existing channel adapter tests still pass — the new field is
> additive and they only assert on `type` / `record`.

---

## Phase 2 — Publish `notification_read` from `mark_read` / `mark_all_read`

**What:** Add an optional `event_bus` parameter to both functions. When
given, publish a `notification_read` event after persisting the read-state
change. Enumerate affected ids; include `unread_count`.

**Files:**
- `src/decafclaw/notifications.py`
  - `mark_read(config, record_id, event_bus=None)` — after persist, if
    `event_bus` is set, unconditionally publish
    `{type: "notification_read", ids: [record_id], unread_count: N}`.
    We deliberately do NOT check the previous state: the extra file read
    to filter no-op calls outweighs the savings, and frontends treat the
    event as an authoritative snapshot anyway (`_count = unread_count`,
    mark-read of an already-read item is a visual no-op).
  - `mark_all_read(config, event_bus=None)` — snapshot the set of
    previously-unread ids *before* the persist (empty snapshot means
    nothing to do); persist; if the snapshot was non-empty and
    `event_bus` is set, publish one event with `ids=snapshot` and
    `unread_count=0`. Empty snapshot → no publish (true no-op is worth
    the cheap check here because "nothing was unread" is a common
    steady-state).
- `tests/test_notifications.py`:
  - Single-id mark-read publishes expected payload.
  - `mark_all_read` publishes one event containing every previously-unread id.
  - `mark_all_read` on an already-fully-read inbox does not publish.
  - Back-compat: both functions work when `event_bus` is not passed (no
    publish attempt, state change lands).

**Verify:** `make lint && make test`.

**After this phase:** Read-state transitions hit the event bus with enough
detail for any subscriber to update in place.

### Prompt

> Extend `src/decafclaw/notifications.py::mark_read` and
> `::mark_all_read` to accept an optional `event_bus` keyword argument.
> Both should persist first (current behavior), then — if `event_bus` is
> provided AND state actually changed — publish a `notification_read`
> event. For `mark_read`, state changes only when the id was previously
> unread; skip publish otherwise (match existing idempotency contract).
> For `mark_all_read`, snapshot the previously-unread-id set before the
> persist; if empty, skip publish; otherwise emit one event with the
> full snapshot as `ids` and `unread_count=0`. Payload shape:
> `{"type": "notification_read", "ids": [...], "unread_count": N}`. Add
> tests for: single id publish, already-read id skips publish, all-read
> publishes one aggregate event, empty inbox skips publish, and no-
> `event_bus` back-compat. `make lint && make test` must pass.

---

## Phase 3 — Thread `event_bus` through REST handlers

**What:** The two mark-read REST handlers in `http_server.py` now pass the
closure's `event_bus` to the notification functions, so actual web-UI
clicks trigger the publish.

**Files:**
- `src/decafclaw/http_server.py` — `notifications_mark_read` and
  `notifications_mark_all_read` pass `event_bus=event_bus` to the
  corresponding `notifications.*` call.
- `tests/test_web_notifications.py` — after POSTing to `/api/notifications/{id}/read`,
  assert a `notification_read` event appeared on a captured event bus.
  Same for `/api/notifications/read-all`.

**Verify:** `make lint && make test`.

**After this phase:** The round trip "user clicks → REST → event fires" is
complete server-side. No WebSocket forwarding yet.

### Prompt

> Wire `event_bus` through the two notification mark-read REST handlers
> in `src/decafclaw/http_server.py`: `notifications_mark_read` and
> `notifications_mark_all_read` should pass their closure's `event_bus`
> to `notifications.mark_read` / `notifications.mark_all_read`. Add
> integration tests in `tests/test_web_notifications.py` that POST to
> both endpoints and assert the corresponding `notification_read` event
> landed on a captured event bus with the right `ids` and
> `unread_count`. Keep existing test coverage (200 response, read state
> persisted) intact. `make lint && make test` must pass.

---

## Phase 4 — WebSocket bridge: forward notification events to the socket

**What:** In `websocket_chat`, subscribe to the event bus for the lifetime
of the connection. A single subscriber callback filters by `event["type"]`
and forwards `notification_created` / `notification_read` events to the
socket as typed JSON messages. Unsubscribe on connection close.

**Files:**
- `src/decafclaw/web/websocket.py`
  - Near the top of `websocket_chat`, after `await websocket.accept()`,
    register an async subscriber:
    ```python
    async def _notif_forward(event: dict):
        t = event.get("type")
        if t == "notification_created":
            await ws_send({
                "type": "notification_created",
                "record": event["record"],
                "unread_count": event["unread_count"],
            })
        elif t == "notification_read":
            await ws_send({
                "type": "notification_read",
                "ids": event["ids"],
                "unread_count": event["unread_count"],
            })
    notif_sub_id = event_bus.subscribe(_notif_forward)
    ```
  - In the connection's cleanup block (`finally`), call
    `event_bus.unsubscribe(notif_sub_id)`. Make sure the id is in scope
    regardless of which branch ran.
- `tests/test_web_notifications.py` (or a new `test_web_websocket_notifications.py`
  if cleaner):
  - Open a test WebSocket, publish `notification_created` to the bus, assert
    the message arrives on the socket with the expected `unread_count`.
  - Same for `notification_read`.
  - Disconnect the socket, publish again, assert no subscriber leak (the
    callback should not fire — inspect bus `_subscribers` dict or count
    invocations of a spy that would catch it).

**Verify:** `make lint && make test`. Run `pytest --durations=10` to make
sure the new tests don't hit any sleep-based flakiness.

**After this phase:** Server pushes notification events to connected
sockets. Frontend still polls — nothing to break yet.

### Prompt

> In `src/decafclaw/web/websocket.py::websocket_chat`, register an
> event-bus subscriber after `await websocket.accept()` that filters by
> `event["type"]` and forwards `notification_created` and
> `notification_read` events to this socket via `ws_send`. Capture the
> subscription id from `event_bus.subscribe(...)` and unsubscribe in the
> connection's `finally` block so no subscribers leak across
> connect/disconnect cycles. Forwarded messages mirror the event
> payload (type, record/ids, unread_count). Add integration tests using
> Starlette's `TestClient` WebSocket support: connect, publish each
> event type on the bus, assert the correct JSON arrives. Add a
> subscriber-leak test: connect+disconnect, then assert a post-
> disconnect publish does not fan out to the closed socket's callback.
> `make lint && make test` must pass.

---

## Phase 5 — Frontend: drop polling, subscribe to window events

**What:** Switch the bell from `setInterval` polling to the shared
WebSocket. `app.js` gains three bridges (two message types plus a
connection-opened signal); `notification-inbox.js` drops the timer and
listens for window events.

**Files:**
- `src/decafclaw/web/static/app.js`
  - In the existing `ws.addEventListener('message', ...)` handler, add two
    branches that re-dispatch notification messages as window custom events
    (`notification-created`, `notification-read`), mirroring the existing
    `turn-complete` pattern.
  - In the existing `ws.addEventListener('open', ...)` handler, dispatch a
    `window.CustomEvent('ws-connected')` in addition to the existing
    banner-hide.
- `src/decafclaw/web/static/components/notification-inbox.js`
  - Remove the `POLL_INTERVAL_MS` constant, the `_pollTimer` field, the
    `setInterval` in `connectedCallback`, and the `clearInterval` in
    `disconnectedCallback`.
  - `connectedCallback`: call `#refreshCount()` once (initial seed). Bind
    and register three window listeners: `ws-connected`,
    `notification-created`, `notification-read`.
  - `disconnectedCallback`: remove those listeners.
  - `ws-connected` handler → `#refreshCount()` (re-seed after any
    reconnect).
  - `notification-created` handler → set `_count = detail.unread_count`;
    if `_open`, call `#fetchList()`.
  - `notification-read` handler → set `_count = detail.unread_count`; if
    `_open`, call `#fetchList()`.
- Keep the existing `#refreshCount` method exactly as-is — it's the one-
  shot seed mechanism.

**Verify:**
- `make check-js` passes (no TS errors from the edits).
- Manual: hard-reload the web UI, open DevTools Network panel, confirm no
  `/api/notifications/unread-count` request fires after the initial one.
- Manual two-tab test: in tab A, trigger `send_notification` from the agent
  (or call the REST endpoint directly). Both tabs' bells update within ~1s.
  In tab A, click the notification to mark read; tab B's count decrements
  without a page refresh.
- Manual reconnect: kill `make dev`, observe `ws-connected` fires on the
  new connection, count re-seeds correctly.

**After this phase:** No polling. Bell is live-updated. Docs update follows.

### Prompt

> Switch the notification bell from polling to WebSocket-driven updates:
> **In `src/decafclaw/web/static/app.js`:** in the existing
> `ws.addEventListener('message', ...)` handler, add two new `msg.type`
> branches that re-dispatch as `window.CustomEvent`:
> `notification_created` → `notification-created` with
> `{detail: msg}`; `notification_read` → `notification-read` with
> `{detail: msg}`. In the existing `ws.addEventListener('open', ...)`
> handler, also dispatch `window.CustomEvent('ws-connected')`.
> **In `src/decafclaw/web/static/components/notification-inbox.js`:**
> remove the 30s polling machinery (`POLL_INTERVAL_MS` const,
> `_pollTimer` field, `setInterval` in `connectedCallback`,
> `clearInterval` in `disconnectedCallback`). Keep the initial
> `#refreshCount()` call as the mount seed. Register window listeners
> in `connectedCallback` and unregister in `disconnectedCallback` for:
> `ws-connected` → `#refreshCount()`; `notification-created` → set
> `_count = detail.unread_count`, re-fetch list if the dropdown is
> open; `notification-read` → same pattern. `make check-js` must pass.
> Manually verify in the web UI that (a) the unread-count endpoint
> stops firing on interval, (b) two tabs stay in sync when one marks
> read, (c) reconnect after `make dev` restart reseeds the count.

---

## Phase 6 — Docs

**What:** Update documentation to reflect the new delivery path.

**Files:**
- `docs/notifications.md` — replace the polling description with the
  WebSocket push architecture; list the new event shapes; note that the
  REST endpoints remain for seed/reconnect.
- `docs/web-ui.md` — if it describes the bell, update the "how state
  refreshes" language.
- `CLAUDE.md` — the "Notification inbox for agent-initiated events" bullet
  mentions polling; change to "WebSocket push with REST seed on connect /
  reconnect." Add a brief note under the "Notification channel adapters"
  bullet about the new WebSocket bridge if it's not already covered.

**Verify:** `make lint` (markdown lint not in make targets today, but eye-
check for broken links and correct file paths).

**After this phase:** Docs match code. Ready for PR.

### Prompt

> Update docs to match the new WebSocket push architecture for the
> notification inbox. In `docs/notifications.md`, replace the polling
> description with: (a) WebSocket push of `notification_created` and
> `notification_read` events from the per-user authenticated socket,
> (b) the REST endpoint `/api/notifications/unread-count` remains as a
> seed on component mount + every reconnect, (c) the `/api/notifications`
> list endpoint still serves the dropdown on open. Update `docs/web-ui.md`
> if it mentions polling. In `CLAUDE.md`'s "Notification inbox for agent-
> initiated events" bullet, swap the poll language for the push
> architecture. Keep changes tight — no rewriting adjacent unrelated
> sections.

---

## Phase 7 — PR + Copilot review

**What:** Open the PR, request Copilot review, link to #332.

**Actions:**
1. Squash all phase commits into one (interactive rebase onto `main`) with
   a single summary commit message referencing `Closes #332`.
2. `git push -u origin notification-inbox-live-sync`.
3. `gh pr create` with Summary / Test plan body. Link `Closes #332`.
4. `gh pr edit N --add-reviewer copilot-pull-request-reviewer`.
5. Verify CI green.

**After this phase:** PR open, Copilot review in flight, issue queued to
close on merge.

---

## Risk register

- **Event-bus fan-out cost.** Each connected WS adds one subscriber. With
  N tabs, every notify fans out to N callbacks sequentially (EventBus uses
  `for ... await`, not gather). A burst of notifications plus many open
  tabs could briefly serialize. Current use is single-user with a handful
  of tabs — acceptable. Rate limiting (#334) mitigates at the producer.
- **Frontend lifecycle races.** If the bell mounts *after* `ws.open` has
  already fired, it misses the `ws-connected` event and only has the
  mount-time `#refreshCount()` seed. That seed is sufficient — we
  re-seed on *every subsequent* reconnect. No race hole.
- **Event ordering.** Event bus publishes sequentially to each subscriber.
  The channel adapters (MM DM, email, vault page) see
  `notification_created` before our new WS forward. If any adapter is
  slow, the WS forward is delayed by its run time. Current adapters all
  dispatch via `asyncio.create_task` (fire-and-forget), so the
  `await callback(event)` returns quickly. Verified by reading the
  existing adapter code; no change needed.
