# Web UI never re-sends SELECT_CONV after a WebSocket reconnect — Spec

**Source:** https://github.com/lmorchard/decafclaw/issues/704

Captured verbatim from the issue body (`<!-- agent-session:spec -->` marker line stripped).

---

## Summary

When `/ws/chat` drops and reconnects, the browser is connected but subscribed to **no conversation stream** — so every server-pushed per-conversation event is silently lost until the user reloads the page.

Found while live-testing #703 (dead terminal tabs); scoped out of that PR because the fix is broader than one feature.

## Mechanism

`lib/websocket-client.js` auto-reconnects and dispatches `open`. The only listener that matters is:

```js
// lib/conversation-store.js:128
this.#ws.addEventListener('open', () => this.listConversations());
```

`SELECT_CONV` is sent **only** from `selectConversation()` (`conversation-store.js:432`), which runs on user navigation — never on reconnect. Server-side, `manager.emit` fans out to per-conversation subscribers, and the new socket isn't one.

## Observed

Reproduced with a browser against a real server restart:

1. Open a conversation, open a canvas tab.
2. Restart the server.
3. Chat WS reconnects (confirmed in server log: `WebSocket /ws/chat [accepted]`, `WebSocket connected: lmorchard`).
4. `POST /api/canvas/{conv}/close_tab` → `200 OK`; server removes the tab and emits `canvas_update`.
5. **Client UI never updates.** The tab stayed on screen indefinitely; a reload showed it correctly gone.

So the two sides silently disagree until reload.

## Blast radius

Anything delivered over the per-conversation stream after a reconnect: `canvas_update`, sticky-slot updates, notifications, tool status, streamed agent output. A reconnect mid-turn likely drops the rest of that turn's output.

## Why it isn't a one-liner

Calling `selectConversation()` on `open` would resubscribe, but it also clears the message store, resets busy/context state, and re-issues `LOAD_HISTORY` — disruptive on every transient blip. This probably wants either a lightweight `RESUBSCRIBE` message type (see `web/message_types.json` + `make gen-message-types`), or a `selectConversation(convId, {resubscribeOnly: true})` path that re-sends `SELECT_CONV` without tearing down client state.

Worth checking whether a resync-on-reconnect is also needed for state that changed while disconnected, not just for future pushes.

## Workaround in place

#703 makes `closeTabById` optimistic (local removal, then POST) so the terminal feature doesn't depend on the push. That's a per-caller patch, not a fix for the class.

---

<!-- Appended by agent-session:triage on 2026-07-29. Author's text above is unchanged. -->

## Verifiable acceptance criteria

- CRITERION: GIVEN a `ConversationStore` with conversation `c1` selected, WHEN its WebSocket
  client dispatches `open` again (a reconnect), THEN the store SHALL send on that socket a
  client→server message that resubscribes it to `c1`, AND that message SHALL carry
  `conv_id === 'c1'`.
  CHECK: `cd src/decafclaw/web/static && npx vitest run lib/conversation-store.test.js` — a new
  test file, authored at freeze. It constructs a fake socket (`class FakeWS extends EventTarget`
  with a recording `send()`), calls `selectConversation('c1')`, clears the record, dispatches a
  second `open`, and asserts the recorded sends contain a message that resubscribes the socket
  with `conv_id === 'c1'`.
  VERIFIED DISCRIMINATING: run at triage against the real module — after the reconnect `open`,
  `sent-after-open = []` (zero messages) while `store.currentConvId` was still `c1`. So the
  behaviour is absent today and the store already retains everything the fix needs.

  **Check-form constraints (measured, not assumed):**
  - `npx vitest run lib/<missing-file>.test.js` exits **1** (`No test files found`), so this
    check cannot pass vacuously by the file being absent.
  - The check MUST NOT use `-t <name>`: a `-t` selection matching nothing exits **0** with every
    test reported skipped. That form grades nothing.
  - The criterion is deliberately implementation-agnostic. Both designs floated in the issue — a
    new `RESUBSCRIBE` message type, or `selectConversation(id, {resubscribeOnly: true})` — satisfy
    it identically, so the choice is implementation style and does not affect the tier.

## Regression guards

These pass today and must keep passing. They grade nothing new and do not affect the tier.

- GUARD: `cd src/decafclaw/web/static && npx vitest run` — the JS unit suite. Invariant: no test
  lost, newly skipped, or newly failing. Observed at triage: `Test Files 6 passed (6) / Tests 42
  passed (42)`.
- GUARD: `uv run pytest tests/test_system_conversations.py` — protects the server-side
  `_handle_select_conv` → `_subscribe_to_conv` contract (`web/websocket.py:173,185`), which matters
  if the implementer adds a new message type. Observed at triage: `19 passed`.
- GUARD: `make check-message-types` — if a new message type is added to `web/message_types.json`,
  the four generated files must be regenerated in the same commit. This work must not hand-edit
  `tui/src/types.generated.ts`.
- GUARD (same new test file): WHEN the socket reopens, the store SHALL NOT discard already-loaded
  messages for the current conversation, and IF no conversation is selected it SHALL NOT send a
  subscribe message at all. Both hold today (nothing happens on reopen), so they are guards, not
  criteria — they exist to rule out the naive
  `addEventListener('open', () => this.selectConversation(id))` fix, which would clear the message
  store and re-issue `LOAD_HISTORY` on every transient blip.

## Tier: `auto-ok`

Neither tier trigger fires. Trigger 1: the criterion has a real oracle that exists now (vitest +
jsdom, `vitest.config.js`, run by `make test-js` and in CI at `.github/workflows/ci.yml:47`); it
discriminates (empty send list, verified by running it); it is not satisfiable without the work
(the cheapest green is actually sending the resubscribe with the right `conv_id`); and no human
judgment is involved. Trigger 2: no risk-gated path — client-side JS plus at most an additive
WebSocket message type. No auth, secrets, data migration/deletion, deploy/infra/CI config, or
dependency change.

## Patterns to follow

`tui/src/wsClient.ts:84-97` (`__reconnected`) + `tui/src/App.tsx:83` already implement exactly this
resubscribe-on-reconnect, tested at `tui/src/wsClient.test.ts:92-138`. Copy its semantics —
**including its documented ordering hazard**: flush the outbound buffer *before* re-sending the
subscribe, or a stale queued `select_conv` lands last and bounces the server's subscription to the
old conversation. The web client has no outbound buffer today (`websocket-client.js:62-68` drops
sends while closed), so the hazard is latent rather than live.

## Verified-false claims

Checked at triage against the code. **Result: none — every concrete claim in this issue is
accurate**, including the verbatim line reference `lib/conversation-store.js:128`. Two additions
the issue does not mention:

- **Omission, not an error.** `web/static/app.js:545-550` also fires on every (re)open and
  dispatches a `ws-connected` window event, which `components/notification-inbox.js:52` already
  uses to re-seed after a drop. That is an existing reconnect-resync wiring point a fix could hang
  off. Separately, `app.js:620-627` registers a **one-shot** `open` handler that calls
  `selectConversation(savedConvId)` and then removes itself — which is why initial-load selection
  works and reconnect selection does not, exactly as reported.
- **Separate defect, out of scope.** `web/static/canvas-page.js:92-97` uses a raw `new WebSocket()`
  with **no reconnect at all**. It does re-send `SELECT_CONV` in its `open` handler but never
  reopens, so the standalone canvas page has a different and worse bug. Left out deliberately; see
  below.

## What we're NOT doing

- **Resyncing state that changed while disconnected.** The issue's closing line ("worth checking
  whether a resync-on-reconnect is also needed for state that changed while disconnected") is
  floated as a question, not a requirement. Reconciling missed `canvas_update` / sticky /
  notification / tool-status state and mid-turn output is a larger piece of work whose criteria
  would depend on an unmade product decision (which state resyncs, and how) — folding it in would
  flip this issue to `needs-review`. **File it as a follow-up.**
- **Fixing `canvas-page.js`.** Separate socket, separate defect, no reconnect logic at all.
- Note for whoever implements: `docs/web-terminal.md:138-146` and the docstring at
  `lib/canvas-state.test.js:8-17` currently document this bug as a known defect and justify the
  #703 optimistic-close workaround. Both should be updated by the fix.
