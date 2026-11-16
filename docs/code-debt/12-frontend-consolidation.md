# Epic 12 — Frontend consolidation

Frontend duplication is lighter but already drifting in at least one spot.

---

### 12.1 — Extract `conversation-list` from `conversation-sidebar.js`

**Size:** M
**Triage:** ready

**Problem.** `conversation-sidebar.js` (985 lines) owns tab switching, the full
conversation/session/folder hierarchy, model/mode pickers, and the config footer.

**Result.** A `conversation-list` component renders the hierarchy (session grouping,
subagent rows, status badges); the parent keeps tab state and slot forwarding.

**Verify.** `make test-js` — `components/conversation-sidebar.test.js`.

**Tests.** Existing sidebar tests pass; move hierarchy assertions to the new
component test.

**Deps.** none

---

### 12.2 — Extract `conversation-controls` from `conversation-sidebar.js`

**Size:** M
**Triage:** ready

**Problem.** The model/mode/context-usage footer is coupled to list rendering.

**Result.** A `conversation-controls` element owns model/mode pickers and the
context meter.

**Verify.** `make test-js` — sidebar + model-catalog tests.

**Tests.** Existing model-selector tests pass; move to the new component test.

**Deps.** none

---

### 12.3 — Stop mirroring store fields into local state

**Size:** S
**Triage:** ready

**Problem.** `conversation-sidebar.js` copies ~10 store fields into local reactive
state via a hand-written `_onStoreChange` diff.

**Result.** `render()` reads store getters directly; the mirror fields are removed.

**Verify.** `make test-js` — `components/conversation-sidebar.test.js`.

**Tests.** Existing rendering tests pass.

**Deps.** none

---

### 12.4 — Extract `lib/panel-url.js` from `app.js`

**Size:** M
**Triage:** ready

**Problem.** `app.js` duplicates the `URLSearchParams` set/get/delete/hide-siblings
dance across 7+ methods (`showWikiPage`, `showFilePage`, `showSchedulePage`, the
hide variants, and `popstate`).

**Result.** A `lib/panel-url.js` module exposes `setPanel`/`clearPanel` and one
sibling-hide helper.

**Verify.** `make test-js` — app-level tests (or add a panel-url unit test).

**Tests.** Add a case asserting panel changes update the URL and hide siblings.

**Deps.** none

---

### 12.5 — Reuse `setupResizeHandle` for wiki/canvas

**Size:** S
**Triage:** ready

**Problem.** The wiki and canvas resize blocks are near-identical mousedown/move/up
trios, while the sidebar already uses `setupResizeHandle`.

**Result.** Both use an extended `setupResizeHandle` (left-edge and right-edge cases).

**Verify.** `make test-js` — canvas/page tests (behavior may need a unit case).

**Tests.** Add a case asserting resize clamps and persists width.

**Deps.** none

---

### 12.6 — Drop the dead error listener in `app.js`

**Size:** XS
**Triage:** ready

**Problem.** `app.js` (~528) registers a `change` listener whose body is empty,
with a comment admitting the mechanism doesn't work.

**Result.** The dead listener is removed; error surfacing continues via the WS
message listener.

**Verify.** `make test-js`.

**Tests.** Existing error-surfacing tests pass.

**Deps.** none

---

### 12.7 — Collapse `wiki-editor.js` endpoint triplication

**Size:** M
**Triage:** ready

**Problem.** `#save`/`#forceSave`/`#reload` each carry three near-identical branches
per endpoint (`/api/vault/`, `/api/config/files/`, schedules), with conflict/error
classification written out nine times.

**Result.** A `SAVE_ENDPOINTS` table (get/put/extractModified per endpoint) drives
a single `#save`/`#forceSave`/`#reload`.

**Verify.** `make test-js` — `components/wiki-editor*.test.js`.

**Tests.** Existing save/reload/conflict tests pass; add a fourth-endpoint case to
prove the table scales.

**Deps.** none

---

### 12.8 — Dedupe `responseError()`

**Size:** S
**Triage:** ready

**Problem.** `responseError(body)` is defined 4× with a subtle ordering drift in
`file-page.js`.

**Result.** One `responseError` in `lib/utils.js`; all four components import it.

**Verify.** `make test-js`.

**Tests.** Add a `lib/utils` unit case for the helper; existing component tests pass.

**Deps.** none

---

### 12.9 — Dedupe model/mode `<select>` rendering

**Size:** M
**Triage:** ready

**Problem.** `chat-input.js`, `conversation-sidebar.js`, and `chat-controls.js` each
render model/mode selectors with diverging "not configured"/"Custom"/"(default)"
adornments (and one binds both `?selected` and `.selected`).

**Result.** A shared render helper (or `<model-select>`/`<mode-select>` element)
with one consistent behavior.

**Verify.** `make test-js` — `components/model-catalog.test.js`, `chat-input.test.js`.

**Tests.** Add a case asserting all three surfaces render the same adornments.

**Deps.** none