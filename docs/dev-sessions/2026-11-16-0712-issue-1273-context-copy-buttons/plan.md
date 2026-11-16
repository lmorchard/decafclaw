# Plan — #1273: Context inspector copy menu → two plain buttons

Base: origin/main @ e4b3f148fb3a. Worktree: `.claude/worktrees/issue-1273-context-copy-buttons`.

## Baseline (pre-change)

- `make test-js` (vitest): 59 files, 681 tests — all pass.
- `make test` (pytest, xdist): 5502 passed, 2 skipped in ~42s. Playwright chromium
  present (`~/Library/Caches/ms-playwright`), browser scenarios ran.

## Steps

1. **Component** (`copy-conversation-menu.js`): remove `_open`, `_menuPosition`,
   `_onDocClick`, doc-click listener, position math, `aria-haspopup`/`aria-expanded`,
   and the `<ul role="menu">` render. Render two adjacent plain buttons — "Copy MD"
   then "Copy JSONL" (decision order) — each calling the unchanged `_copy(format)`.
   Keep: tag `copy-conversation-menu`, class name, `convId` property + falsy guard,
   `_copy()` fetch/clipboard/toast flow. Update the doc comment (was a floating menu
   until #1273).
2. **Mount** (`context-inspector.js`): tag unchanged; refresh the comment above the
   mount to reference #1273.
3. **CSS**: remove dead `.copy-menu-*` rules from `styles/chat.css` (trigger, list,
   items, mobile label-hide media query) and the orphaned `.copy-menu-trigger` rule in
   `styles/chat-controls.css`. Add compact button sizing in `styles/context-inspector.css`
   mirroring `.copy-raw-btn` (0.75rem / 0.2rem 0.6rem), wrapped in a small inline-flex
   group so the two buttons sit adjacent with a tight gap.
4. **Tests**:
   - `context-export.test.js`: update the raw-pane mount test to assert two buttons +
     labels and no menu remnants; add click-through tests (button → fetch URL with the
     right format + clipboard + toast); keep the `_copy` unit tests (still valid).
   - `tests/test_web_browser_scenarios.py`: keep `test_browser_conversation_export`;
     delete `test_browser_copy_menu_position` (#1224 — the floating-menu positioning it
     protected no longer exists; CLAUDE.md: rewrite/remove, don't keep deprecated tests).
   - `chat-controls.test.js`: unchanged (tag name kept; its absence assertion stays valid).
5. **Docs** (`docs/web-ui.md`): update the three copy-menu descriptions (raw-tab bullet,
   "Copy conversation" section, generated-methods paragraph) for the two-button UI.
6. **Gates**: `make fmt`, targeted vitest, full `make test`, `make check`.
7. **Commit** logical steps; publish draft PR via submit-pr (handoff authorization).

## Evidence limits to watch

- Browser scenarios need Playwright chromium — present locally; CI installs it.
- Live UI feel (button spacing, mobile fit) is a human assessment for Les; unit +
  browser tests establish the mechanics.
