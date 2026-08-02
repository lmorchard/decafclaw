# Session notes — #704 resubscribe on reconnect

## Run shape

Two sessions on one branch. The first (2026-08-02 09:43) ran `express` Phase 0 + plan steps 1–5
and stopped after the freeze commit — `checks.md`, the frozen test file, and G5's new test file
exist; no `plan.md`, no implementation, clean tree. The second (this one) resumed rather than
starting fresh.

**Why resume.** Session setup's step 2 says to ask. Unattended, the answer isn't a coin flip:
the freeze was authored by a context that had never seen an implementation plan, which is exactly
the property `frozen-checks.md` exists to protect. Re-freezing in the context that then writes
the code would have weakened the oracle to buy nothing.

Before touching anything, re-ran the whole frozen set to confirm the recorded state still held:
C1 failed with the byte-identical recorded assertion, G1 `10 files / 87 tests, 1 failed | 86
passed`, G2+G5+G6 `22 passed`, G3 clean. All matched `checks.md`.

## Design decision

**Reuse `select_conv`; do not add a `resubscribe` wire type.** Both designs were floated in the
issue and the criterion is deliberately agnostic between them.

- **Why:** `_handle_select_conv` already calls `_subscribe_to_conv` (`web/websocket.py:173,185`),
  and the client's `CONV_SELECTED` handler is idempotent (`conversation-store.js:585-594` — sets
  `read_only`, restores a pending confirmation). So the existing type already does the whole job.
  It also matches the TUI's existing fix (`tui/src/App.tsx:83`), which is the pattern the spec
  told us to copy.
- **Rejected:** a new `resubscribe` type. It would mean a manifest entry, four regenerated files,
  a new server handler, and a second code path that subscribes — all to reach behaviour an
  existing type already has. The freeze anticipated this: G5 exists precisely to catch a new type
  registered without a handler.
- **Consequence:** G3 (`make check-message-types`) is **vacuous** for this design — it passes
  without carrying information, because nothing touched the manifest. `checks.md` records this;
  repeating it here so a reader of the PR's green G3 row doesn't over-read it.

## Things checked that could have been bugs

- **Duplicate confirmation cards on reconnect.** The resubscribe makes the server re-send
  `conv_selected`, which may carry `pending_confirmation` — and the client feeds that into
  `ToolStatusStore`. If that path didn't dedupe, every blip would stack another confirmation card.
  It does: `tool-status-store.js:189-193` dedupes on `confirmation_id`, with a comment already
  anticipating the same message arriving twice by different routes. No change needed.
- **Double-send on the *initial* connect.** `app.js:615-628` registers a one-shot `open` handler
  that performs the first selection. On the first open `#currentConvId` is still `null`, so
  `#resubscribe()` returns early and the one-shot handler does its normal job. No duplicate
  `select_conv`, no behaviour change on first load.
- **Read-only / system conversations.** `#readOnly` is only reset inside `selectConversation()`,
  which the resubscribe deliberately doesn't call. The server re-sends `read_only: true` for those
  convs, so the flag stays correct across a reconnect.

## Latent hazard, recorded not fixed

The TUI documents an ordering hazard (`tui/src/wsClient.ts:88-95`): flush the outbound buffer
*before* re-sending the subscribe, or a stale queued `select_conv` lands last and bounces the
server's subscription to the old conversation. The web client has **no outbound buffer** —
`websocket-client.js:63-68` drops sends while the socket is closed — so nothing can be queued to
land after the resubscribe. The hazard is latent, not live. Noted in `plan.md` and in the code
comment so that adding an outbound buffer later doesn't silently reintroduce it.

## Deferred / out of scope

Both were ruled out by the spec's "What we're NOT doing", not discovered here:

- **Resync of state that changed while disconnected.** This PR makes *future* pushes arrive; it
  does not reconcile `canvas_update` / sticky / notification / tool-status state missed during the
  gap, nor recover mid-turn output. That needs a product decision about which state resyncs and
  how, which is why folding it in would have flipped the tier. Worth a follow-up issue.
- **`canvas-page.js` (`/canvas/{conv_id}`).** Separate raw socket with no reconnect logic at all
  (`:92-97`) — it re-sends `SELECT_CONV` on open but never reopens. Different and worse defect.
  Mentioned in the `docs/web-ui.md` note so it isn't mistaken for covered.

## Docs touched

- `docs/web-terminal.md` and the `canvas-state.test.js` docstring both cited this bug as the
  justification for #703's optimistic canvas-tab close. The workaround is still correct — the
  reconnect lands on its own backoff, long after the close was POSTed — but the stated reason had
  become false, so both were rewritten rather than deleted.
- `docs/web-ui.md` gained a short subsection under REST vs WebSocket: subscriptions are
  per-socket and re-established on reconnect, why it isn't `selectConversation()`, and the
  canvas-page carve-out.
