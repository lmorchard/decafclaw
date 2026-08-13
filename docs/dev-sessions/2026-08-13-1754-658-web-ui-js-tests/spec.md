## Context

While planning #654 (selectable color themes) we discovered the web UI has **no JS test runner at all** — zero `.test.js` files, no vitest/jest, no pytest-playwright browser harness. All 21 Lit components and every module under `web/static/lib/` are verified only by `make check-js` (`tsc --checkJs`) plus manual verification.

#654 introduces **vitest + jsdom** as the first JS test infrastructure, with real unit tests for the theme registry/apply/persistence logic. That establishes the runner and the pattern — but only covers the theme code.

## Goal

Backfill JS unit-test coverage for the existing untested web UI logic now that a runner exists.

## Good early candidates (pure-ish logic, high value)

- `lib/` state/store modules: `canvas-state.js`, `sticky-state.js`, `conversation-store.js`, `message-store.js`, `tool-status-store.js`, `widget-catalog.js`, `message-types.js`
- `lib/utils.js`, `lib/markdown.js` (parsing/formatting helpers)
- Component logic that's separable from rendering (e.g. registry/mapping/reducer functions)

## Notes

- Prefer extracting pure logic into `lib/` modules (testable) over testing Lit render output directly.
- Consider wiring `make test-js` into the standard `make check` / CI gate once coverage is meaningful.
- Depends on the vitest setup landing in #654.

Follow-up to #654.

## Verifiable acceptance criteria

- CRITERION: WHEN state and store modules (`message-store.js`, `tool-status-store.js`, `widget-catalog.js`) are tested, THE SYSTEM SHALL have vitest unit tests covering state mutations, updates, and event notifications.
  CHECK: `npm --prefix src/decafclaw/web/static test` runs successfully and executes tests for these store modules.
  VERIFIED DISCRIMINATING: No dedicated unit test files exist for `message-store.js` or `tool-status-store.js` currently.

- CRITERION: WHEN utility modules (`lib/utils.js`, `lib/markdown.js`) are tested, THE SYSTEM SHALL have vitest unit tests validating parsing, formatting, and sanitization helper functions.
  CHECK: `npm --prefix src/decafclaw/web/static test` runs successfully and executes dedicated utility test suites.
  VERIFIED DISCRIMINATING: No unit test files exist for `lib/utils.js` or `lib/markdown.js` currently.

- CRITERION: WHEN complex component write-path state machine logic (such as in `wiki-page.js` or extracted pure helpers) is tested, THE SYSTEM SHALL have unit tests verifying mutex locking, pending-fields accumulation, and conflict resolution/retry bookkeeping.
  CHECK: `npm --prefix src/decafclaw/web/static test` runs successfully and executes write-path state machine tests.
  VERIFIED DISCRIMINATING: No unit tests exist for `wiki-page.js` write-path coordination currently.

## Regression guards
- GUARD: `npm --prefix src/decafclaw/web/static run build` (`tsc --checkJs`) passes.
- GUARD: `npm --prefix src/decafclaw/web/static test` passes all existing test suites (123 tests).

## Tier: auto-ok

(All criteria are machine-checkable via vitest and the task does not touch risk-gated paths such as auth, secrets, or deployment config.)

