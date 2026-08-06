# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/755
**Frozen at:** d5df31a
**Check files — read-only from Phase 1 onward:**
- `src/decafclaw/web/static/components/chat-input.test.js`

## C1
CRITERION: WHEN `_highlight` changes to index N while the command menu is open, THE SYSTEM SHALL call `scrollIntoView` exactly once, on the `.command-menu-item` element whose `data-command` equals `matches[N].name`, and the first argument SHALL deep-equal `{ block: "nearest" }`.
CHECK: `npx vitest run components/chat-input.test.js` (test: "scrolls the highlighted item into view with block: 'nearest' when highlight moves (C1)") passes.
AT FREEZE: fails — `AssertionError: expected "vi.fn()" to be called 1 times, but got 0 times` (correct reason: `scrollIntoView` is never called when highlight moves).

## C2
CRITERION: WHEN the highlight wraps — ArrowUp from index 0 with N matches — THE SYSTEM SHALL target the row at index N-1, not index 0.
CHECK: `npx vitest run components/chat-input.test.js` (test: "scrolls to the last item when highlight wraps on ArrowUp from index 0 (C2)") passes.
AT FREEZE: fails — `AssertionError: expected "vi.fn()" to be called 1 times, but got 0 times` (correct reason: `scrollIntoView` is never called when highlight wraps).

## Guards

- G1: `npx vitest run components/chat-input.test.js components/chat-input-caret.test.js` from `src/decafclaw/web/static` — 2 files, 22 pre-existing tests pass at freeze.

## Adjudication

- C1: accepted — verifies `scrollIntoView` call on the highlighted menu item with `{ block: "nearest" }`.
- C2: accepted — verifies wrap case targets row index N-1.
- G1: accepted — pre-existing 22 tests pass at freeze.
