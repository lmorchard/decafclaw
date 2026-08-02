# Resubscribe on WebSocket reconnect — Implementation Plan

**Goal:** After `/ws/chat` drops and reconnects, re-subscribe the fresh socket to the
already-selected conversation so server-pushed per-conversation events keep arriving without a
page reload.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/704 — **Tier:** `auto-ok`
(criterion has a real oracle — vitest + jsdom, already run by `make test-js` and in CI — verified
discriminating at triage and again at the freeze; no human judgment; no risk-gated path — client
JS only, no auth/secrets/migration/deploy/dependency change)

**Approach:** Add a second `open` listener in `ConversationStore`'s constructor, alongside the
existing `listConversations()` one, that re-sends `SELECT_CONV` for `#currentConvId` when a
conversation is selected. Reuse the existing `select_conv` wire type rather than introducing a
`resubscribe` type: `_handle_select_conv` already calls `_subscribe_to_conv`
(`web/websocket.py:173,185`), the client's `CONV_SELECTED` handler is idempotent
(`conversation-store.js:585-594` — sets `read_only`, restores any pending confirmation), and this
mirrors the TUI's existing fix at `tui/src/App.tsx:83`. No `LOAD_HISTORY` and no client-state
teardown, so a transient blip does not blank the transcript the user is reading.

**Criteria:** C1 — a reconnect `open` re-sends a registered client→server subscribe message
carrying `conv_id === 'c1'`.

Full text + checks live in `checks.md`. Ids are assigned there and referenced here.

---

## Phase 0: Freeze the acceptance checks — **COMPLETE**

Done in a prior session on this branch. Recorded here so the resume point is unambiguous.

**Files:**
- Created: `docs/dev-sessions/2026-08-02-0943-704-resubscribe-on-reconnect/checks.md`
- Created: `src/decafclaw/web/static/lib/conversation-store.test.js` — the test C1 names
- Created: `tests/test_ws_message_type_handlers.py` — G5, added at freeze step 4

**Verification — automated:**
- [x] C1's check runs and fails for the expected reason — `AssertionError: reconnect #1: nothing
      sent for c1: expected [] to not have a length of +0`, exit 1, 4 tests collected (1 failed /
      3 passed). Re-confirmed at the start of this session, byte-identical.
- [x] Every guard runs and passes — G1 `10 files / 87 tests, 1 failed | 86 passed` (the single
      failure being C1 by design); G2+G5+G6 `22 passed`; G3 `git diff --exit-code` clean.
      Re-confirmed at the start of this session, all matching the freeze record.
- [x] Check-reviewer dispatched read-only; `## Adjudication` in `checks.md` carries one
      disposition per check and per guard (C1 strengthened, G1 escalated, G2 accepted-with-
      correction + G6 added, G3 accepted, G4 strengthened, G5/G6 accepted).
- [x] Freeze commit `5bd6188`; sha recorded in follow-up commit `f5e0299`.

**Read-only from Phase 1 onward:**
- `src/decafclaw/web/static/lib/conversation-store.test.js`
- `tests/test_ws_message_type_handlers.py`

---

## Phase 1: Re-send `SELECT_CONV` on reconnect

Adds the resubscribe. One vertical slice: the store's socket-lifecycle wiring, the wire message
it sends, and the two docs that currently document the absence of this behaviour as a known
defect.

**Advances:** C1 (fully). Nothing remains for a later phase.

**Files:**
- Modify: `src/decafclaw/web/static/lib/conversation-store.js` — add a `#resubscribe()` private
  method and a second `open` listener in the constructor that calls it.
- Modify: `docs/web-terminal.md` (~:139-147) — the first "load-bearing detail" bullet asserts
  `conversation-store` "never re-sends `SELECT_CONV`". After this phase that is false. Keep the
  bullet (the optimistic-close behaviour is still correct and still load-bearing) and rewrite its
  justification: the socket is down at the moment `closeTabById` runs, so the push cannot arrive
  in time regardless of whether a later reconnect resubscribes.
- Modify: `src/decafclaw/web/static/lib/canvas-state.test.js` (docstring, :7-17) — same stale
  claim, same rewrite. This file is **not** a frozen check file, so editing it is permitted; only
  its prose changes, no assertion.

**Key changes:**

`conversation-store.js` — constructor, after the existing listeners at :127-128:

```js
    this.#ws.addEventListener('open', () => this.listConversations());
    // A reconnect gives us a brand-new socket, and the server tracks stream
    // subscriptions per socket — so without this, every per-conversation push
    // (canvas_update, sticky, tool status, streamed output) is delivered to
    // nobody until a full page reload. Deliberately NOT selectConversation():
    // that clears the message store and re-issues LOAD_HISTORY, which would
    // blank the transcript and refetch 50 messages on every transient blip.
    this.#ws.addEventListener('open', () => this.#resubscribe());
```

and the method itself, next to `selectConversation()`:

```js
  /**
   * Re-subscribe the (re)connected socket to the current conversation.
   *
   * Reuses `select_conv` rather than a bespoke wire type because
   * `_handle_select_conv` already subscribes the socket (`web/websocket.py:173`)
   * and its `conv_selected` reply is idempotent for the client. Mirrors the
   * TUI's `__reconnected` handler (`tui/src/App.tsx:83`).
   *
   * No-op when nothing is selected: there is no stream to rejoin, and the
   * initial connect is handled by app.js's one-shot open handler.
   */
  #resubscribe() {
    if (!this.#currentConvId) return;
    this.#ws.send({ type: MESSAGE_TYPES.SELECT_CONV, conv_id: this.#currentConvId });
  }
```

Ordering note: on the *first* open, `#currentConvId` is still `null`, so this sends nothing and
`app.js:615-628`'s one-shot handler performs the initial selection exactly as it does today. The
`websocket-client.js` `send()` drops messages while the socket is closed (`:63-68`) and there is
no outbound buffer, so the TUI's documented flush-before-resubscribe ordering hazard
(`tui/src/wsClient.ts:88-95`) is latent here, not live — nothing can be queued to land after the
resubscribe. Recorded so a future outbound buffer doesn't reintroduce it silently.

**Verification — automated:**
- [ ] C1's check passes: `cd src/decafclaw/web/static && npx vitest run lib/conversation-store.test.js`
      — target `4 tests, 4 passed`, exit 0
- [ ] G1 still passes: `cd src/decafclaw/web/static && npx vitest run` — target
      `10 files / 87 tests, 87 passed`, exit 0. Read the printed counts, not just the exit code:
      per the G1 adjudication, deletion and `describe.skip` both exit 0.
- [ ] G2 still passes: `uv run pytest tests/test_system_conversations.py` — target `19 passed`
- [ ] G3 still passes: `make check-message-types` — expected clean, and expected **vacuous**:
      this design touches neither `message_types.json` nor any generated file, so a clean G3
      carries no information here beyond "nothing was hand-edited"
- [ ] G5 still passes: `uv run pytest tests/test_ws_message_type_handlers.py` — target `2 passed`
- [ ] G6 still passes: `uv run pytest tests/test_web_websocket_workflow.py` — target `1 passed`
- [ ] `make check` passes (lint + pyright + `tsc --checkJs` + message-type drift)
- [ ] `make test` passes (no regression)
- [ ] Tamper diff empty: `git diff 5bd6188 -- src/decafclaw/web/static/lib/conversation-store.test.js tests/test_ws_message_type_handlers.py`

**Verification — manual:**
- [ ] None. C1 is fully automated and no criterion is human-judgment, which is what makes this
      run `auto-ok`. The end-to-end behaviour the issue reports (restart the server, close a
      canvas tab, watch the UI update without a reload) is worth a human spot-check after merge
      per the repo's "test live in the web UI after merging" convention, but it is not a gate
      item and no criterion depends on it.
