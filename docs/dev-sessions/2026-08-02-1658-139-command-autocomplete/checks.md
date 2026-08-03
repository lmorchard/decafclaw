# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/139

**Frozen at:** `a0a95e2` (2026-08-02) — the tamper-diff baseline.
**Check files — read-only from Phase 1 onward:**
- `src/decafclaw/web/static/components/chat-input.test.js`
- `src/decafclaw/web/static/lib/command-list.test.js`
- `tests/test_web_command_list.py`

## Settled design decision — the request rides on conversation-select

The spec settles the transport (a new WebSocket message type) but not *when* the client asks. Three
answers were available and are not interchangeable. **Decided by Les, 2026-08-02: Option B.**

| Option | Cost | Verdict |
|---|---|---|
| **A. Request on socket `open`;** narrow the #704 guard to conversation-scoped frames. | Edits another issue's regression test to fit this feature. Its *stated* intent is resubscription, which narrowing preserves — but its literal assertion covers all traffic. | **Rejected** — #704 was fixed the same day; weakening its guard to fit a new feature is not that trade. |
| **B. Request on conversation-select.** | The menu is empty for the first message of a fresh session. Reachable: `sendMessage` creates a conversation when none exists (`conversation-store.js:470`). | **Chosen.** Cost explicitly accepted. |
| **C. Request lazily** on the first trigger keypress. | A round-trip on the first `/`, so the menu can render a beat late. Moves the trigger out of the store's established push-a-list idiom. | Not chosen. |

Settled **before the freeze commit**, so revising C3 from "on `open`" to "on select" was a pre-freeze
revision, not an amendment. **No tier change — this run remains `auto-ok`.**

`lib/conversation-store.test.js:238` is untouched. C3's last case (`stays silent on a reconnect when no
conversation is selected`) asserts the same invariant from this side, so the two files agree rather than
merely coexist — and it fails loudly if anyone later moves the request back to the open handler to buy
back the empty-first-message cost.

## C1

CRITERION: GIVEN a mounted `chat-input` whose command source lists `help` and `mcp__demo__summarize`,
WHEN the user types `/mc` at the start of the line, THEN the component SHALL render a suggestion list
containing `mcp__demo__summarize` (fuzzy subsequence match), AND WHEN `ArrowDown`/`ArrowUp` are
pressed the highlighted entry SHALL change, AND WHEN `Tab` is pressed the textarea value SHALL become
`/mcp__demo__summarize ` and the menu SHALL close.

CHECK: `cd src/decafclaw/web/static && npx vitest run components/chat-input.test.js`

AT FREEZE: fails. Collected 15 cases, so the check had teeth (not a vacuous zero-collection pass).
The run quoted below (`Tests 6 failed | 5 passed (11)`) was captured *before* the four check-reviewer
strengthenings landed; the frozen file has 15 cases and fails 8 — see the Clarification under
Amendments. The six failures quoted are the behaviour genuinely being absent, not an import or path
error, and the four strengthenings added two more failures of the same kind
(`commits the moved highlight on Tab, not the first match`,
`opens on a trigger starting a later line, not just the whole value`):
- `offers the fuzzy matches for "/mc"` — `AssertionError: expected null not to be null` (no `.command-menu`)
- `matches by subsequence, not by prefix` — `expected [] to deeply equal [ 'mcp__demo__summarize' ]`
- `opens on "!" at the start of the line` — `expected null not to be null`
- `highlights the first suggestion and moves it with ArrowDown/ArrowUp` — `expected [] to deeply equal [ 'help', 'mcp__demo__summarize' ]`
- `commits the highlighted suggestion on Tab` — `expected '/mc' to be '/mcp__demo__summarize '`
- `dismisses the menu on Escape` — `expected null not to be null`

The five passing cases are the frozen guard content, deliberately green at freeze: the three G2
menu-closed regressions (Enter sends `{text, attachments}`, Shift+Enter does not send, Escape while busy
dispatches `stop`) plus two that pass vacuously today and gain teeth once the menu exists (`stays closed
when the trigger is not at the start of the line`, `keeps Enter as send while the menu is open`).

## C2

CRITERION: WHEN a web client requests the invokable-command list over the WebSocket, the server SHALL
respond with a structured list that includes each skill command and **every MCP prompt as
`mcp__<server>__<prompt>`**, each carrying its description and argument hint.

CHECK: a pytest node over the new handler, **with `get_registry` monkeypatched to a fake registry
exposing one prompt with one required and one optional argument**, asserting the response contains the
skill command and an entry `mcp__demo__summarize` whose hint is `<text> [language]`.

RESOLVED COMMAND (the concrete realization of the CHECK above; the prose is the oracle, this is how it
is run): `uv run pytest tests/test_web_command_list.py`

AT FREEZE: fails — `3 failed in 1.49s`, exit 1 (not exit 5; three nodes were collected, so the check is
not vacuous). All three fail at `handler = websocket._HANDLERS[REQUEST_TYPE]` with
`KeyError: 'list_commands'` — the module imports cleanly and the fake-registry fixture builds fine, so
the failure is the absent handler, not collection breakage.

## Contract defined by the frozen checks

(The checks were authored before any implementation, so they define the observable contract. Recorded
here because the implementation must build to it — this is a description of the frozen tests, not a
separate oracle.)

- Component property: `chat-input.commands`, an Array of `{name, description, argument_hint}`.
- Selectors: container `.command-menu` (rendered only while open — absence means closed); rows
  `.command-menu-item` with `data-command="<name>"`; highlight is class `highlighted` on the row,
  exactly one while open, index 0 on open.
- Wire types: client→server `list_commands`; server→client `command_list` with
  `{"type": "command_list", "commands": [{"name", "description", "argument_hint"}]}`.
- Handler: `_handle_list_commands(ws_send, index, username, msg, state)` in
  `src/decafclaw/web/websocket.py`, registered in `_HANDLERS` under `WSMessageType.LIST_COMMANDS`.
- `get_registry` must be resolved at call time (the call-time import `commands.py` already uses), not
  bound into `websocket.py` at import — a module-level binding would dodge the monkeypatch.

## C3

(Added at the freeze review, closing an escalation the check-reviewer raised against C2 — see
Adjudication. Not a criterion from the issue; it is the coverage without which C1 and C2 are both
satisfiable by a feature that is dead in the browser.)

CRITERION (derived from C2's "WHEN a web client requests the invokable-command list over the
WebSocket"): the client SHALL actually request the list over the socket, and the reply SHALL reach the
`chat-input` component's command source.

CHECK: `cd src/decafclaw/web/static && npx vitest run lib/command-list.test.js`

AT FREEZE: fails — 8 tests, `7 failed | 1 passed`. Six are behavioural over `ConversationStore` using
the existing `FakeWS` pattern (`declares both wire types in the manifest`; `starts with an empty command
list`; `requests the command list when a conversation is selected`; `re-requests on every reconnect, not
just the first`; `exposes the commands from a command_list frame and notifies subscribers`; `keeps the
{name, description, argument_hint} entry shape intact`). Observed failures:
`expected undefined to be 'client_to_server'`; `expected undefined to deeply equal []`;
`sent ["select_conv","load_history"]: expected [...] to include 'list_commands'`;
`reconnect #1 sent ["select_conv"]: expected [...] to include 'list_commands'`; `TypeError: Cannot read
properties of undefined (reading 'find')`. The two `sent [...]` messages are the useful ones — they show
the real store path running and emitting its existing frames, so the failure is the absent request, not
a broken harness.

The one passing case is `stays silent on a reconnect when no conversation is selected`, which passes
**vacuously today** (nothing is sent at all). It is an anti-regression guard for the Option B decision,
not red-to-green work, and it acquires teeth the moment the request exists.

The seventh, `assigns store.commands onto the chat-input element`, is a **TEXT-BASED assertion over
`app.js` source**, paired with the behavioural six above. `app.js` is a top-level DOM script with no
unit-mountable seam, so this is the only available coverage for the final hop — and a rename or reflow
can flip it independently of behaviour. Do not read it as behavioural coverage. Observed: `no line in
app.js assigns store.commands to chatInput.commands — the server reply never reaches the component`.
(It reads the source via a Vite `?raw` import rather than `fs`: under jsdom `import.meta.url` is an
`http:` URL and `fileURLToPath` throws. It carries its own harness guard so a broken import blames
itself rather than `app.js`.)

**C3 is the check the settled decision above is about.** Its request assertion was revised pre-freeze
from "on socket `open`" to "when a conversation is selected".

## Guards

(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- G1: `cd src/decafclaw/web/static && npx vitest run` — the JS suite.
  **Invariant: no pre-existing test is lost, newly skipped, or newly failing.** Tests this work adds are
  not a violation of it.
  *Observations, for orientation only — never the pass condition:* the ten pre-existing files measured
  `87 passed / 0 skipped` before the check files were added; with them present the tree read
  `2 failed | 10 passed (12) files`, `15 failed | 95 passed (110)`, which reconciles exactly
  (`110 − 23 new = 87` pre-existing).
  (The issue's `6 files / 42 tests` is from triage on 2026-07-29; `origin/main` has advanced since.)
  Residual the command cannot close: `vitest run` exits 0 when a file is deleted or a test is
  `.skip`ped, so grade the invariant by comparing the pre-existing files' results, not by observing
  green.
- G2: existing composer behaviour is unchanged when the menu is closed — `Enter` sends,
  `Shift+Enter` newlines, `Escape` stops while busy. Covered by assertions inside the frozen
  `chat-input.test.js` (menu-closed cases), so it is graded by C1's command.
  **Passed at freeze: see C1's AT FREEZE — the menu-closed cases pass while the autocomplete cases fail.**
- G3: `uv run pytest tests/test_web_websocket_commands.py tests/test_commands.py` — command dispatch
  through the socket keeps working. **Invariant: the web path still accepts both `!` and `/`,
  Mattermost stays `!`-only, and no test here is lost, newly skipped, or newly failing.**
  *Observation:* `34 passed in 1.24s` at freeze — `30` before the strengthening added four tests that
  actually exercise the prefix split (see Adjudication).
- G4: `make check-message-types` — a new WebSocket message type means a `web/message_types.json` entry
  plus regenerated `message_types.py`, `lib/message-types.js`, `docs/websocket-messages.md` and
  `tui/src/types.generated.ts`, all in the same commit. Do not hand-edit the generated files.
  **Passed at freeze: `git diff --exit-code` over the four generated files returned clean.**
- G5: full Python suite — `make test`.
  **Invariant: no pre-existing test is lost, newly skipped, or newly failing, and the suite exits 0.**
  Tests this work adds are not a violation of it.
  *Observations, for orientation only — never the pass condition:* `3717 passed, 2 skipped` before the
  check file was added (the issue marked this UNRUN; it has now been run); `6 failed, 3721 passed,
  2 skipped` with it, the four G3 additions passing on top of the 3717 and the six new C2 nodes failing.
  Teeth worth naming: G5 is the guard that carries `test_message_types.py`,
  `test_ws_message_type_handlers.py` and `test_discovered_skills_consumers.py` — so a new
  `config.discovered_skills` read in `websocket.py` with no recorded trust-tier decision fails here.

## Adjudication

Graded before the freeze commit by an independent read-only check-reviewer (no Edit/Write), given
`checks.md` and the repo but not the plan and not the criteria's rationale. Its remit was one question
per item: *what could make this check green that is not the work its criterion names?* Every
strengthening below was applied before the freeze, so none of them is an amendment and none costs the
tier.

- **C1: strengthened** — four gameable holes, each with a named cheap fake:
  1. Tab was only ever exercised on a one-match query (`/mc`), so `commit(matches[0])` — never reading
     the highlight — greened it and drained the Arrow test too. Added: from `/` (two matches),
     `ArrowDown` then `Tab` must yield `/mcp__demo__summarize `, which the always-first fake fails.
  2. "Subsequence not prefix" ruled out prefix and substring but not *order*:
     `[...q].every(ch => name.includes(ch))` passed every case. Added a negative case whose characters
     are all present but out of order.
  3. "Start of the line" was only tested as start of the *value*; `value.startsWith('/')` greened it and
     newlines were never exercised. Added a `hello\n/mc` case, which is what the criterion literally says.
  4. Nothing asserted Tab is *not* swallowed with the menu closed, so an unconditional
     `preventDefault()` greened C1 while permanently breaking tab-out of the composer. Added a
     menu-closed `defaultPrevented === false` assertion.
- **C2: strengthened** — three holes:
  1. No `user_invocable` filter was forced (the fixture had one invocable skill and lookups ignore
     extras), so returning every discovered skill greened it. Added a non-invocable skill that must be
     absent from the reply.
  2. Every asserted value was a literal in the test, so a hardcoded two-element reply passed. Added a
     second prompt from a *different* server, forcing the `mcp__<server>__<prompt>` name to be computed.
  3. The response type was a bare string bound to nothing — `make check-message-types` cannot catch a
     server→client type that never entered the manifest, because regeneration produces no diff when the
     manifest never changed. Added `WSMessageType.LIST_COMMANDS`/`COMMAND_LIST` equality assertions.
- **C3: added (escalation from C2)** — the reviewer found that C1 and C2 could both be fully green with
  a feature that is *dead in the browser*: nothing asserted the client ever sends `list_commands`, nor
  that a `command_list` reply reaches `chat-input.commands`. Not fixable inside either file, so a third
  frozen check was authored over `ConversationStore` (behavioural, using the existing `FakeWS` pattern)
  plus one explicitly-labelled text assertion over `app.js`'s binding, which has no unit-mountable seam.
- **G1: strengthened** — the recorded baseline `10 files / 87 tests` was captured *before* the frozen
  files were added and so is unreproducible in the freeze tree; restated below against the pre-existing
  ten files, with the post-implementation target named separately. Note the residual the command cannot
  close: `vitest run` exits 0 when a test file is deleted or a test is `.skip`ped, so the count *is* the
  guard and must be compared, not just observed green.
- **G2: accepted** — the reviewer tried three fakes and none worked. `toEqual` on the whole `send` detail
  rejects partial or extra-keyed payloads; the Shift+Enter case asserts both no-send *and* that the text
  survives, so an early return that clears the box fails; the Escape case mounts `busy: true`, the exact
  state the new Escape branch contends for, and asserts the `stop` event actually fired. All three
  assert resulting state rather than names, and they live inside the frozen file, so they cannot be
  deleted while C1's command stays green.
- **G3: strengthened** — the guard claimed to cover the `!`/`/` transport split and could not: its one
  web test monkeypatches `dispatch_command` away entirely and never sends `/`, and `test_commands.py`
  only exercises `parse_command_trigger` with the prefix passed in explicitly. Narrowing the web call
  site to `prefixes=["!"]` would have killed every `/command` in the browser at `30 passed`. Added two
  behavioural tests (default prefix set accepts both; a narrowed set rejects `/help`), a captured-kwarg
  assertion on the web call site, and one explicitly-labelled text assertion pinning
  `prefixes=["!"]` in `mattermost.py` (its call site is buried in the message handler with no cheap
  seam). **Probed:** applying the reviewer's exact regression to `websocket.py` turned the guard red
  (`Right contains one more item: '/'`); the probe was reverted with a targeted edit, and
  `git diff --stat` confirms `websocket.py` is unmodified.
- **G4: strengthened** — half of what G4 names is enforced by G5's `test_message_types.py` (the
  client→server half), and the server→client half was enforced nowhere: a reply frame built as a plain
  dict with a literal `"command_list"` type greens C2, greens `make check-message-types`, and greens G5,
  while the manifest, `lib/message-types.js`, `docs/websocket-messages.md` and
  `tui/src/types.generated.ts` never learn the type exists. Closed by C2's new `WSMessageType`
  assertions.
- **G5: strengthened** — same defect as G1: `make test` collects `tests/test_web_command_list.py`, so
  `3717 passed, 2 skipped` predates the frozen file and is unreproducible in the freeze tree. Restated
  below. Recorded in G5's favour: it is the guard that actually carries `test_message_types.py`,
  `test_ws_message_type_handlers.py` and `test_discovered_skills_consumers.py` — so a new
  `config.discovered_skills` read in `websocket.py` without a recorded trust-tier decision fails here.

## Amendments

(Append-only. Empty unless an amendment was made.)

**None.** No CRITERION line, CHECK command, or guard command changed after `a0a95e2`.

### Clarification (not an amendment; no tier change)

The implementer reported that C1's and G1's recorded counts were stale: C1's AT FREEZE said
"Collected 11 cases" when the frozen file has **15**, and G1 said "22 new / 109 total" when the real
figures are **23 new / 110 total**. The gap is exactly the four C1 strengthenings the Adjudication
section describes adding (moved-highlight Tab, out-of-order negative, `hello\n/mc`, Tab-not-swallowed)
— the counts were captured before those landed and never refreshed.

This is a **clarification** by the mechanical test in `frozen-checks.md`: it changes no CRITERION line,
no CHECK command, and no guard command, and re-running the old and new wording against both the freeze
commit and the current implementation changes no verdict at either tree — the numbers are recorded
observations, and G1's pass condition is the count-free invariant, not the number. Corrected in place
above; logged here so the edit isn't mistaken for a silent rewrite of the freeze record.

## Tamper verdict

(Recorded at the end of execute and again in pr.)

(pending)
