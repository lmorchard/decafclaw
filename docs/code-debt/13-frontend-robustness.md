# Epic 13 — Frontend robustness & generated-code policy

---

### 13.1 — Standardize REST error policy

**Size:** M
**Triage:** ready

**Problem.** REST callers silently swallow `ApiError` (e.g. `conversation-store.js`
~282) while others `console.error` differently; a 500 on `listConversations` leaves
the sidebar empty with no feedback.

**Result.** A small shared result/error wrapper returns `{ok, error}` uniformly;
callers surface errors to the user rather than swallowing them.

**Verify.** `make test-js` — `lib/conversation-store.test.js`.

**Tests.** Add a case asserting a failed list call surfaces an error state the UI
can render (no silent swallow).

**Deps.** none

---

### 13.2 — Add a WS outbound queue

**Size:** M
**Triage:** ready

**Problem.** `WebSocketClient.send()` silently drops messages when not connected
(`console.warn; return`), so a send during reconnect is lost with no user signal.

**Result.** An outbound queue buffers sends until `open` (or surfaces a toast on drop).

**Verify.** `make test-js` — `lib/message-store.test.js` / websocket-client tests.

**Tests.** Add a case asserting a send while disconnected is queued and delivered on
open (or reported, not silently dropped).

**Deps.** none

---

### 13.3 — Replace native `prompt()/confirm()/alert()`

**Size:** M
**Triage:** ready

**Problem.** `conversation-sidebar`, `vault-sidebar`, `file-page`, `wiki-page`, and
`schedule-page` use native dialogs (blocking, unbranded, untestable) instead of the
project's confirm/toast primitives.

**Result.** Folder/rename/delete flows use the existing confirmation UX.

**Verify.** `make test-js` — component tests (dialog now a testable element).

**Tests.** Convert alert-based assertions to element-based assertions.

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