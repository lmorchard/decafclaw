# Implementation Plan

## Phase 0: Freeze checks
- [x] Write `checks.md` with verifiable acceptance tests.
- [x] Commit freeze checks.

## Phase 1: State and Store Modules Tests
**Advances:** C1
- Implement unit tests for `message-store.js`, covering state mutations, updates, and event notifications.
- Implement unit tests for `tool-status-store.js`, covering state mutations, updates, and event notifications.
- Implement unit tests for `widget-catalog.js`, covering state mutations, updates, and event notifications.
- [ ] VERIFY: `cd src/decafclaw/web/static && npx vitest run lib/message-store.test.js lib/tool-status-store.test.js lib/widget-catalog.test.js` passes.

## Phase 2: Utility Modules Tests
**Advances:** C2
- Implement unit tests for `lib/utils.js`, validating parsing, formatting, and sanitization helper functions.
- Implement unit tests for `lib/markdown.js`, validating parsing, formatting, and sanitization helper functions.
- [ ] VERIFY: `cd src/decafclaw/web/static && npx vitest run lib/utils.test.js lib/markdown.test.js` passes.

## Phase 3: Component Write-Path Logic extraction and tests
**Advances:** C3
- Extract complex write-path state machine logic (mutex locking, pending-fields accumulation, conflict resolution/retry bookkeeping) from `wiki-page.js` into a testable pure helper `lib/wiki-page-write-mutex.js`.
- Refactor `wiki-page.js` to use `lib/wiki-page-write-mutex.js`.
- Implement unit tests in `components/wiki-page.test.js` (and/or `lib/wiki-page-write-mutex.test.js` if we rename) to cover the state machine logic without requiring DOM rendering for the pure logic.
- [ ] VERIFY: `cd src/decafclaw/web/static && npx vitest run components/wiki-page.test.js` passes.
