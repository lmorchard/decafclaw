# Spec — #1273: Context inspector copy menu → two plain buttons

Issue: https://github.com/lmorchard/decafclaw/issues/1273

## Background

The context inspector's raw pane mounts `<copy-conversation-menu>` (moved there in
#1192). It renders a `📋 Copy ▾` trigger that opens a floating, position-fixed
dropdown with two items: "Copy as JSONL" and "Copy as markdown". The issue author
saw only the markdown item (stale cache) and asked for a plain button.

Triage (2026-11-15) found the menu actually has two options, so a single button
would silently drop JSONL export. Les's decision on the issue (2026-11-15):

> replace the menu with **two adjacent plain buttons** — "Copy MD" and
> "Copy JSONL". Both exports stay; the floating menu goes.

## Intended result

- The raw pane shows two adjacent plain buttons: **Copy MD** and **Copy JSONL**.
- No floating menu: no trigger/caret, no dropdown list, no fixed-position
  popover machinery (the #1224 positioning behavior is retired with it).
- Each button keeps the existing export flow: `GET /api/conversations/{id}/export?format=markdown|jsonl`
  → `navigator.clipboard` → toast on success/failure. Endpoints unchanged.
- The component still hides itself when `convId` is falsy (no active conversation).

## Scope

| File | Change |
| --- | --- |
| `src/decafclaw/web/static/components/copy-conversation-menu.js` | Replace menu render + open/position machinery with two adjacent plain buttons; keep `_copy(format)`, `convId` property, tag name |
| `src/decafclaw/web/static/components/context-inspector.js` | Mount stays; update the comment to reference #1273 |
| `src/decafclaw/web/static/styles/chat.css` | Remove dead `.copy-menu-*` rules (trigger, list, items, mobile label hide) |
| `src/decafclaw/web/static/styles/chat-controls.css` | Remove dead `.copy-menu-trigger` rule (already orphaned by #1192, fully dead now) |
| `src/decafclaw/web/static/styles/context-inspector.css` | Add compact sizing for the two buttons, matching `.copy-raw-btn` vocabulary |
| `src/decafclaw/web/static/components/context-export.test.js` | Update mount test; add render/click tests for the two buttons; keep `_copy` unit tests |
| `tests/test_web_browser_scenarios.py` | Keep export scenario; retire `test_browser_copy_menu_position` (#1224 — the behavior it protected no longer exists) |
| `docs/web-ui.md` | Update the three copy-menu descriptions for the two-button UI |

Out of scope: export endpoints, markdown/JSONL rendering, chat-controls layout
(the menu already left that strip in #1192).

## Success conditions and evidence

1. **Two plain buttons render in the raw pane** — vitest: assert both buttons
   with their labels; assert no `.copy-menu-list` / `aria-haspopup` remains.
2. **Clicking each button exports the right format** — vitest: click through the
   rendered buttons, assert fetch URL (`?format=markdown` / `?format=jsonl`) and
   clipboard + toast. Browser scenario (real Chromium, real endpoint) already
   covers `_copy` end-to-end and stays.
3. **Hides with no conversation** — existing behavior; covered by the component's
   falsy-convId render guard (assert in vitest).
4. **No dead menu CSS** — rules removed; `make check` (incl. browser-assets
   module-graph test) passes.
5. **Docs current** — `docs/web-ui.md` describes the two-button UI in the same PR.
