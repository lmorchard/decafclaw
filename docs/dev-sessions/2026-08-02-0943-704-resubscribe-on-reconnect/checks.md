# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/704
**Frozen at:** `5bd6188` (2026-08-02) — the tamper-diff baseline.
**Check files — read-only from Phase 1 onward:**
- `src/decafclaw/web/static/lib/conversation-store.test.js`
- `tests/test_ws_message_type_handlers.py`

## C1

CRITERION: GIVEN a `ConversationStore` with conversation `c1` selected, WHEN its WebSocket
client dispatches `open` again (a reconnect), THEN the store SHALL send on that socket a
client→server message that resubscribes it to `c1`, AND that message SHALL carry
`conv_id === 'c1'`.

CHECK: `cd src/decafclaw/web/static && npx vitest run lib/conversation-store.test.js` — a new
test file, authored at freeze. It constructs a fake socket (`class FakeWS extends EventTarget`
with a recording `send()`), calls `selectConversation('c1')`, clears the record, dispatches a
second `open`, and asserts the recorded sends contain a message that resubscribes the socket
with `conv_id === 'c1'`.

AT FREEZE: **fails**, exit 1. 4 tests collected, 1 failed / 3 passed.

```
FAIL lib/conversation-store.test.js > ConversationStore reconnect handling >
     resubscribes the reopened socket to the selected conversation
AssertionError: reconnect #1: nothing sent for c1: expected [] to not have a length of +0
 ❯ expectResubscribed lib/conversation-store.test.js:140:58
```

Correct reason: the behaviour is genuinely absent. All 4 tests collected and ran, so the
manifest JSON import, the `fetch` stub, and the fake socket all work. The failing assertion is
the *second* one in `expectResubscribed` — the preceding conversation-list-refresh assertion
(line 133) passed, so the `open` handler did fire and did do its existing work; it simply sent
nothing on the socket. `ws.sent` is literally `[]`. Matches `conversation-store.js:128`, where the
`open` listener does nothing but `listConversations()` (a REST fetch). The three passing tests
(manifest-harness guard, G4a, G4b) confirm the fixture observes "zero sends" as a real state
rather than a broken recorder.

**Check-form constraints (copied verbatim from the issue; measured, not assumed):**
- `npx vitest run lib/<missing-file>.test.js` exits **1** (`No test files found`), so this
  check cannot pass vacuously by the file being absent.
- The check MUST NOT use `-t <name>`: a `-t` selection matching nothing exits **0** with every
  test reported skipped. That form grades nothing.
- The criterion is deliberately implementation-agnostic. Both designs floated in the issue — a
  new `RESUBSCRIBE` message type, or `selectConversation(id, {resubscribeOnly: true})` — satisfy
  it identically, so the choice is implementation style and does not affect the tier.

## Guards

G1–G4 are copied verbatim from the issue. G5 and G6 were **added at freeze step 4** by the
check-reviewer's adjudication (see `## Adjudication`); they are additive and pass today, so they
change no issue-sourced text and cost no tier.

- G1: `cd src/decafclaw/web/static && npx vitest run` — the JS unit suite. Invariant: no test
  lost, newly skipped, or newly failing. Observed at triage: `Test Files 6 passed (6) / Tests 42
  passed (42)`.
  AT FREEZE: **passes** (before the check file was added) — `Test Files 9 passed (9) / Tests 83
  passed (83)`, exit 0. With the frozen check file present: `10 files / 87 tests, 1 failed | 86
  passed`, the single failure being C1 by design.
  Drift from triage's `6 (6) / 42 (42)`: `origin/main` grew three JS test files and 41 tests
  between 2026-07-29 and this run. The invariant is "no test lost, newly skipped, or newly
  failing" relative to *this* baseline, not equality with the triage-era count. Recorded so a
  later reader doesn't read the growth as tampering.
  **At verification the target is `10 files / 87 tests, 87 passed, exit 0`** — 86 pre-existing
  passes plus C1 flipping green. Any lower count is a lost or skipped test.
- G2: `uv run pytest tests/test_system_conversations.py` — protects the server-side
  `_handle_select_conv` → `_subscribe_to_conv` contract (`web/websocket.py:173,185`), which matters
  if the implementer adds a new message type. Observed at triage: `19 passed`.
  AT FREEZE: **passes** — `19 passed in 1.29s`, exit 0. Collected 19, matching triage.
  **Correction on record (see adjudication):** this guard does *not* protect the
  `_subscribe_to_conv` contract the issue claims it does. The `ws_state` fixture
  (`tests/test_system_conversations.py:134-141`) supplies `config`, `event_bus`, `app_ctx`,
  `websocket` and **no `manager`**, and `_subscribe_to_conv` opens with
  `manager = state.get("manager"); if not manager: return` (`websocket.py:635-637`). So its body is
  dead code in all 19 tests, which exercise `_handle_select_conv`'s *response and authorization*
  branches only. Kept verbatim and still a valid guard for what it does cover; G6 covers the
  subscription contract.
- G3: `make check-message-types` — if a new message type is added to `web/message_types.json`,
  the four generated files must be regenerated in the same commit. This work must not hand-edit
  `tui/src/types.generated.ts`.
  AT FREEZE: **passes** — regenerated all four files, `git diff --exit-code` clean, exit 0.
  Note: G3 is **vacuous for a `select_conv`-reuse design**, which touches neither the manifest nor
  any generated file. "G3 green" carries information only if the manifest actually changed.
- G4 (same new test file): WHEN the socket reopens, the store SHALL NOT discard already-loaded
  messages for the current conversation, and IF no conversation is selected it SHALL NOT send a
  subscribe message at all. Both hold today (nothing happens on reopen), so they are guards, not
  criteria — they exist to rule out the naive
  `addEventListener('open', () => this.selectConversation(id))` fix, which would clear the message
  store and re-issue `LOAD_HISTORY` on every transient blip.
  AT FREEZE: **passes** — both tests green inside the otherwise-failing check file.
  `keeps already-loaded messages when the socket reopens` (G4a) and
  `sends nothing on the socket when no conversation is selected` (G4b).
- G5 (**added at freeze**): `uv run pytest tests/test_ws_message_type_handlers.py` — every
  `client_to_server` type declared in `src/decafclaw/web/message_types.json` has an entry in
  `decafclaw.web.websocket._HANDLERS`. Closes the reviewer's highest-value residual hole: an
  implementer who registers a new `resubscribe` type in the manifest (satisfying C1's type
  assertion *and* G3 after regeneration) but forgets the server handler, leaving `_dispatch` to
  fall through to `ws: unknown inbound message type` and return an error frame — every check
  green, bug unfixed.
  AT FREEZE: **passes** — `2 passed`, exit 0 (not exit 5; collected 2). Teeth verified by
  commenting out `WSMessageType.WIDGET_RESPONSE` in `_HANDLERS`: the test failed naming
  `widget_response` specifically, and `websocket.py` was restored with an empty diff.
- G6 (**added at freeze**): `uv run pytest tests/test_web_websocket_workflow.py` — the only test
  file in the repo that constructs a real `manager` and observes `conv_subscriptions`, i.e. the
  server-side half of "resubscribes it" that G2 turns out not to reach.
  AT FREEZE: **passes** — `1 passed in 1.34s`, exit 0.

Combined python guards at freeze (G2 + G5 + G6): `22 passed in 1.34s`, exit 0.

## Step-4 re-confirmation of the spec's own evidence

Not an acceptance check — this is `plan.md` step 4, confirming the gap the issue describes is
still present at the branch point (`c594311`) before any test was authored.

Ad-hoc repro (`/tmp/repro-704.mjs`, deliberately outside the repo): construct a `FakeWS`
`EventTarget` with a recording `send()`, stub `globalThis.fetch`, `new ConversationStore(ws)`,
`selectConversation('c1')`, clear the record, dispatch a second `open`. Observed:

```
currentConvId = "c1"
sent-after-open = []
```

Byte-identical to the issue's `VERIFIED DISCRIMINATING` finding. The behaviour is still absent and
the store still retains everything a fix needs.

## Adjudication

Written at freeze step 4, before the freeze commit. A read-only reviewer (no Edit/Write) was given
`checks.md` and the repo, and *not* the criteria's rationale or any implementation plan — none
existed yet. Its remit was one question per check and per guard: *what could make this green that
is not the work its criterion names?* Every claim below was independently re-verified against the
code before being acted on.

- **C1: strengthened.** The reviewer found four ways to green it without the work, all confirmed:
  1. `send({type: 'resubscribe', conv_id})` with no manifest entry and no handler — the invented
     type sits outside the deny-list so it counted as a subscribe, while the real server
     (`websocket.py:1006-1011`) logs `ws: unknown inbound message type` and replies with an error
     frame. Also `send({conv_id: 'c1'})` with no `type` at all, and echoing a *server→client* type
     like `conv_selected`. **Closed** by deriving the legal set from
     `src/decafclaw/web/message_types.json` (`direction === 'client_to_server'`) and asserting
     membership — a manifest-derived set rather than the generated `MESSAGE_TYPES` constants,
     because a renamed type makes `MESSAGE_TYPES.GONE` evaluate to `undefined` and silently shrink
     the deny-list, and `static/tsconfig.json:25` excludes `**/*.test.js` so nothing would flag it.
  2. A one-shot handler (`if (done) return; done = true; …`) greened it because C1 fired `open`
     only once — while leaving every *later* reconnect broken, which is the bug's actual
     user-visible shape (repeated transient blips). **Closed** by firing `open` twice and
     re-asserting, with `reconnect #N:` prefixed on every failure message.
  3. Rewriting `conversation-store.js:128` rather than adding alongside it greened C1 and both G4
     tests while silently killing conversation-list refresh on reconnect. **Closed** by asserting
     a `fetch` of `/api/conversations` (read from `listConversations()`,
     `conversation-store.js:181-186`) after each reopen. Deliberately ordered first so C1's
     present-day failure message stays about the missing resubscribe.
  4. Line 95's `expect(subscribes[0].conv_id).toBe('c1')` was a tautology — `subscribes` derives
     from a list already filtered on `conv_id === 'c1'`, so it could never fail. It read as rigor.
     **Closed** by replacing it with the membership assertion from (1).

  The CHECK *command* is byte-identical to the issue's. The strengthened test is a strict superset
  of the shape the issue's CHECK prose describes; recording that here so a later reader doesn't
  read the extra assertions as an unlogged edit.

- **C1 / `LOAD_HISTORY` deny-listing: accepted, deliberately.** The reviewer correctly found the
  deny-list's stated premise is wrong: `_handle_load_history` *does* call `_subscribe_to_conv`
  (`websocket.py:320`), as does `_handle_send` (`:423`). So a `load_history`-only fix would
  resubscribe server-side and satisfy the criterion's letter, yet C1 grades it red. Kept red on
  purpose: refetching 50 messages on every transient blip is precisely the disruption G4 exists to
  forbid, and the spec's "What we're NOT doing" rules out resync-on-reconnect. G4a's added
  no-`LOAD_HISTORY` assertion makes the two consistent rather than contradictory.

- **G1: escalated.** Its command mechanically detects only *newly failing*. Verified: deleting a
  test file yields `9 passed (9)`, exit 0; `describe.skip` yields `skipped`, exit 0; narrowing
  `vitest.config.js` `include` yields fewer files, exit 0. "No test lost, newly skipped" is
  enforced by **reading the printed counts against the frozen baseline**, which the independent
  verifier must do explicitly — not by the exit code. Command kept verbatim (changing it would be
  an amendment); the baseline and the verification target are written into G1 above so the
  comparison is mechanical for the reader even though it isn't for the shell. Mitigating: C1's
  explicit-file argument means an `include`/`exclude` narrowing that hides the check file fails C1
  with exit 1 (`No test files found`), so that particular dodge is covered.

- **G2: accepted as written, with a correction on record, plus G6 added.** The reviewer's finding
  that G2 does not reach `_subscribe_to_conv` is confirmed — no `manager` in the fixture, so the
  function returns at its first line in all 19 tests. Deleting the `_subscribe_to_conv` call at
  `websocket.py:173` or `:185` would leave all 19 green. G2's command is issue-sourced and stays
  verbatim; the correction is recorded in G2 above, and **G6** adds
  `tests/test_web_websocket_workflow.py`, the only file that actually observes
  `conv_subscriptions`.

- **G3: accepted.** The reviewer tried committing a hand-edit to `tui/src/types.generated.ts`
  (reverted by the generator, `git diff --exit-code` nonzero), staging without committing (same),
  a pathspec dodge (all four paths are tracked, none ignored), and a partial edit in a
  generator-untouched region (none exists — each file is rewritten whole). Only escape is editing
  the manifest so the generator reproduces the content, which is the intended path. Recorded
  above: G3 is vacuous for a `select_conv`-reuse design, and it runs only the drift half of
  `make check`, not `pyright`/`tsc`.

- **G4: strengthened.** G4b resisted every attempt (unconditional send, `conv_id: ''`,
  `conv_id: null` all fail it) — accepted as-is. G4a asserted only message *content*, so a
  `selectConversation(id, {resubscribeOnly: true})` that skips `messageStore.clear()` but still
  fires `LOAD_HISTORY` passed while refetching 50 messages per blip, and an implementation that
  nulls `#currentConvId` while leaving the array intact also passed. **Closed** by adding: zero
  `LOAD_HISTORY` sends after the reopen, `store.currentConvId === 'c1'`, and a re-assert after a
  second reopen. The reviewer also confirmed G4's placement *inside* the C1 check file is a
  feature, not a problem — one invocation enforces C1 and both guards, so an implementation cannot
  green C1 by regressing G4. Cost: the exit code alone can't say which failed, and the manifest
  bans `-t`; the adjudicator reads the per-test lines (today: 4 tests, 1 failed, 3 passed).

- **G5: accepted** (added at freeze; teeth verified by deliberate breakage, see G5 above).

- **G6: accepted** (added at freeze; passes today, `1 passed`).

### Residual holes, named rather than closed

- G5 proves a registered type is *dispatchable*, not that its handler *subscribes*. An implementer
  could add `resubscribe` to the manifest and point `_HANDLERS` at an existing non-subscribing
  handler (`_handle_cancel_turn`, `_handle_set_model`, `_handle_confirm_response`,
  `_handle_widget_response`) and pass everything. Judged not worth closing: the plausible reuse
  targets (`_handle_select_conv`, `_handle_load_history`, `_handle_send`) all *do* subscribe, so
  the hole requires a deliberately perverse choice. Closing it properly needs a whole-stack smoke
  or a new server-side integration test against a type that does not exist yet at freeze.
- C1's conversation-list assertion checks that a `fetch` of `/api/conversations` happened during
  the reconnect, not that it came from line 128 specifically. An implementation that dropped that
  line but called `listConversations()` from a new resubscribe path still passes — acceptable,
  since list refresh on reconnect is the behaviour being protected.
- C1 is a jsdom unit test and cannot observe that the message reaches the server or that the
  server honours it. G6 covers the server-side subscription contract; nothing in the set is a
  whole-stack test, which is the standing limitation of a client-side criterion.

## Amendments

_(Append-only. Empty — no amendment was made. All changes above were made at freeze step 4,
before the freeze commit, which is why none costs the tier.)_

## Tamper verdict

Recorded at `pr` step 5, against the tree that ships. **Verdict: `clean`** — not
`clean-by-substitute`; `Check files` is non-empty, so the diff command is meaningful rather than
vacuous.

```
git diff 5bd6188 -- src/decafclaw/web/static/lib/conversation-store.test.js tests/test_ws_message_type_handlers.py
```

Empty output. Both frozen check files are byte-identical to the freeze commit. Confirmed twice:
once at the end of `execute` by the independent verifier (fresh context, given only this file and
the repo), and again here immediately before pushing.

No rebase was needed — `origin/main` did not advance during the run — so `5bd6188` was never
rewritten and needs no re-anchoring. `git merge-base --is-ancestor 5bd6188 HEAD` succeeds, and the
branch is pushed unsquashed, so a reviewer can re-run the command above themselves rather than
taking this record on trust.

Collateral check: `git diff 5bd6188 --stat` lists only `checks.md` (sanctioned appends),
`plan.md`, `notes.md`, `docs/web-terminal.md`, `docs/web-ui.md`,
`src/decafclaw/web/static/lib/canvas-state.test.js` (docstring prose only, no assertion), and
`src/decafclaw/web/static/lib/conversation-store.js` (the fix). No frozen check file appears.
