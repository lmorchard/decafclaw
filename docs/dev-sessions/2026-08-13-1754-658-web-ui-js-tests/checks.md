# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/658
**Frozen at:** (pending)
**Check files — read-only from Phase 1 onward:**
- `src/decafclaw/web/static/lib/message-store.test.js`
- `src/decafclaw/web/static/lib/tool-status-store.test.js`
- `src/decafclaw/web/static/lib/widget-catalog.test.js`
- `src/decafclaw/web/static/lib/utils.test.js`
- `src/decafclaw/web/static/lib/markdown.test.js`
- `src/decafclaw/web/static/components/wiki-page.test.js`

## C1
CRITERION: WHEN state and store modules (`message-store.js`, `tool-status-store.js`, `widget-catalog.js`) are tested, THE SYSTEM SHALL have vitest unit tests covering state mutations, updates, and event notifications.
CHECK: `cd src/decafclaw/web/static && npx vitest run lib/message-store.test.js lib/tool-status-store.test.js lib/widget-catalog.test.js` passes.
AT FREEZE: fails — `AssertionError: expected "Not implemented" to be "fail"`

## C2
CRITERION: WHEN utility modules (`lib/utils.js`, `lib/markdown.js`) are tested, THE SYSTEM SHALL have vitest unit tests validating parsing, formatting, and sanitization helper functions.
CHECK: `cd src/decafclaw/web/static && npx vitest run lib/utils.test.js lib/markdown.test.js` passes.
AT FREEZE: fails — `AssertionError: expected "Not implemented" to be "fail"`

## C3
CRITERION: WHEN complex component write-path state machine logic (such as in `wiki-page.js` or extracted pure helpers) is tested, THE SYSTEM SHALL have unit tests verifying mutex locking, pending-fields accumulation, and conflict resolution/retry bookkeeping.
CHECK: `cd src/decafclaw/web/static && npx vitest run components/wiki-page.test.js` passes.
AT FREEZE: fails — `AssertionError: expected "Not implemented" to be "fail"`

## Guards
- G1: `cd src/decafclaw/web/static && npx tsc --noEmit` passes.
- G2: `cd src/decafclaw/web/static && npx vitest run` passes all existing test suites.

## Adjudication
- C1: accepted — the tests do not exist yet.
- C2: accepted — the tests do not exist yet.
- C3: accepted — the tests do not exist yet.
- G1: accepted — passes today.
- G2: accepted — passes today.

## Amendments
