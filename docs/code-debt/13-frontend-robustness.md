# Epic 13 — Frontend robustness & generated-code policy

UI acceptance here uses the project's Playwright browser harness
(`tests/test_web_browser_scenarios.py`, `browser_scenario` fixture), which runs
under `make test`/CI — no human, no live service. This keeps the items
unattended-fit.

---

### 13.1 — Standardize REST error policy

**Size:** M
**Triage:** ready

**Problem.** REST callers silently swallow `ApiError` (e.g. `conversation-store.js`
~282) while others `console.error` differently; a 500 on `listConversations` leaves
the sidebar empty with no feedback.

**Result.** A shared result/error wrapper returns `{ok, error}` uniformly; callers
expose the error to the UI instead of swallowing it. The sidebar renders an error
state on a failed list call.

**Scope.** In: `lib/api.js` wrapper + the conversation-store call sites. Out: WS
path (13.2).

**Verify.** `make test-js` for the store unit plus a Playwright scenario:
`make test` — `tests/test_web_browser_scenarios.py`.

**Tests.** Unit: a failed `listConversations` produces a store error state. Browser:
a scenario stubs a 500 on `listConversations` and asserts the sidebar shows an
error node (not silently empty).

**Deps.** none

---

### 13.2 — Add a WS outbound queue

**Size:** M
**Triage:** ready

**Problem.** `WebSocketClient.send()` silently drops messages when not connected
(`console.warn; return`), so a send during reconnect is lost with no user signal.

**Result.** An outbound queue buffers sends until `open`, then flushes; a message
that cannot be delivered is surfaced as an error state rather than dropped.

**Scope.** In: `WebSocketClient` + the message store's send path. Out: no protocol
change.

**Verify.** `make test-js` plus a Playwright scenario: `make test` —
`tests/test_web_browser_scenarios.py`.

**Tests.** Unit: a send while closed is queued and delivered on open. Browser: a
scenario sends before the socket opens and asserts the message arrives (or the UI
shows a not-sent state, never a silent drop).

**Deps.** none

---

### 13.3 — Replace native `prompt()/confirm()/alert()`

**Size:** M
**Triage:** ready

**Problem.** `conversation-sidebar`, `vault-sidebar`, `file-page`, `wiki-page`, and
`schedule-page` use native dialogs (blocking, unbranded, untestable) instead of the
project's confirm/toast primitives.

**Result.** Folder/rename/delete flows use the existing confirmation UX; the
confirmation state is a DOM element a test can assert on.

**Scope.** In: replace the native-dialog call sites with the confirm/toast
primitives. Out: no server-side confirmation change.

**Verify.** `make test-js` plus a Playwright scenario: `make test` —
`tests/test_web_browser_scenarios.py`.

**Tests.** Unit: the confirm/toast element renders with the right text. Browser: a
scenario drives a folder rename and asserts the branded confirm appears (instead
of a native `prompt`/`confirm`, which Playwright cannot observe — proving the
replacement).

**Deps.** none

---

### 13.4 — Annotate/gate the generated `api-client/index.js` boundary

**Size:** S
**Triage:** ready

**Problem.** `lib/api-client/index.js` (1,953 lines) is a committed bundle of `.ts`
sources; the generated-vs-authored boundary is unmarked, risking drift.

**Result.** A header comment marks it generated; `make check-js` gates drift via the
`gen-api-client` → `git diff` pattern already used for message types.

**Verify.** `make check-js` (prove it fails on a deliberate edit).

**Tests.** The gate; existing client-contract tests pass.

**Deps.** none