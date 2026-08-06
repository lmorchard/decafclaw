# Command suggestion menu does not scroll the highlighted item into view

**Source:** https://github.com/lmorchard/decafclaw/issues/755

## Current state

Cursoring through the command suggestions with ArrowDown/ArrowUp moves the highlight, but the menu does not scroll. Once the highlight passes the bottom of the visible area it is off-screen, and you are navigating a list you cannot see.

The menu is a scroll container (`src/decafclaw/web/static/styles/chat-input.css:17-18`): `max-height: 14rem; overflow-y: auto;`. The arrow handlers only move an index (`src/decafclaw/web/static/components/chat-input.js`), Lit re-renders with the `highlighted` class, and nothing ever scrolls. `scrollIntoView` and `scrollTop` do not appear anywhere under `src/decafclaw/web/static/`.

## Verifiable acceptance criteria

- CRITERION 1: WHEN `_highlight` changes to index N while the command menu is open, THE SYSTEM SHALL call `scrollIntoView` exactly once, on the `.command-menu-item` element whose `data-command` equals `matches[N].name`, and the first argument SHALL deep-equal `{ block: "nearest" }`.
  CHECK: `npx vitest run components/chat-input.test.js` from `src/decafclaw/web/static`.
  VERIFIED DISCRIMINATING: No `scrollIntoView` calls exist in static web assets; Lit `updated()` hook is absent on `chat-input.js`.

- CRITERION 2: WHEN the highlight wraps — ArrowUp from index 0 with N matches — THE SYSTEM SHALL target the row at index N-1, not index 0.
  CHECK: `npx vitest run components/chat-input.test.js` from `src/decafclaw/web/static`.
  VERIFIED DISCRIMINATING: Fails until test and implementation exist.

## Regression guards

- GUARD: `npx vitest run components/chat-input.test.js components/chat-input-caret.test.js` from `src/decafclaw/web/static` — existing arrow-navigation and Tab-commit behavior is unchanged. Passes today.

## Tier: auto-ok

The criterion names a specific mechanism on a specific element, the harness exists (vitest), and it fails today. The check verifies the call and not the visual result (jsdom has no layout).
