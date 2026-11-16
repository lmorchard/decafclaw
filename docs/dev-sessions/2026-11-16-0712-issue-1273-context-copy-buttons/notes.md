# issue-1273-context-copy-buttons

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1273
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1273-context-copy-buttons
- Branch: issue-1273-context-copy-buttons
- Base: origin/main at e4b3f148fb3a4cb63b0121af7eca65265e63109c
- Next action: DONE — implementation complete, all gates green; published as draft PR.

## Decision (from issue thread)

Les (2026-11-15): replace the floating menu with **two adjacent plain buttons** —
"Copy MD" and "Copy JSONL". Both exports stay; the menu goes. Triage had found
the menu actually has two options (JSONL + markdown), so a single button would
have silently dropped JSONL export.

## Baseline (origin/main @ e4b3f148fb3a, pre-change)

- `make test-js` (vitest): 59 files / 681 tests — all pass.
- `make test` (pytest, xdist): 5502 passed, 2 skipped in ~42s.
- Playwright chromium present at `~/Library/Caches/ms-playwright` (macOS cache;
  `~/.cache/ms-playwright` does not exist on darwin).

## Changes

- `components/copy-conversation-menu.js`: removed `_open` / `_menuPosition` /
  doc-click listener / position math / `aria-haspopup` + the `<ul role="menu">`;
  renders two adjacent plain buttons (Copy MD, Copy JSONL) calling the unchanged
  `_copy(format)` fetch/clipboard/toast flow. Tag name and class kept (decision
  preserves the file); doc comment records the history. Falsy-convId hide guard kept.
- `components/context-inspector.js`: mount unchanged; comment updated to #1273.
- `styles/chat.css`: removed dead `.copy-menu-wrap/-trigger/-list/-item` rules +
  mobile label-hide media query.
- `styles/chat-controls.css`: removed orphaned `.copy-menu-trigger` rule (already
  dead since #1192 moved the menu out of the strip).
- `styles/context-inspector.css`: added `.copy-conv-actions` (inline-flex, 0.25rem
  gap) + `button.copy-conv-btn` compact sizing mirroring `.copy-raw-btn`.
  Note: `dc-small-btn` is used by the inspector but defined nowhere (pre-existing;
  Pico default + scoped rules provide the styling) — left as-is, not this issue's scope.
- `components/context-export.test.js`: mount test now asserts two buttons + labels
  and no menu remnants; added click-through tests (button → fetch URL per format +
  clipboard + toast) and a falsy-convId render guard test. Kept the `_copy` unit
  tests (still valid).
- `tests/test_web_browser_scenarios.py`: kept `test_browser_conversation_export`
  (real Chromium, real endpoint); deleted `test_browser_copy_menu_position` — the
  #1224 floating-menu positioning behavior no longer exists (CLAUDE.md: remove
  deprecated tests, don't keep them).
- `docs/web-ui.md`: updated the three copy-menu descriptions for the two-button UI.

## Verification

- New-test specificity: with the old component restored, exactly the 3 new
  behavior tests fail (2 click-through + mount); the falsy-guard test passes on
  old code too (it guards pre-existing behavior). ✓
- `make fmt`: no changes needed. `make check`: all green (npm policy, message-types
  drift, fmt-check, ruff, pyright 0/0, tsc --noEmit, module graph).
- `make test-js`: 684 passed (baseline 681 + 3 new).
- `make test`: first post-change run had ONE failure in
  `tests/test_shell_auto_background.py::test_above_budget_returns_job_handle_same_process`
  (`'error' != 'stopped'`) — unrelated subsystem (shell background jobs; diff touches
  only web static + docs). Flake: the file passes in isolation (13/13) and a full
  re-run was green: **5501 passed, 2 skipped** (baseline 5502 minus the retired
  #1224 scenario).

## Remaining / human assessment

- Live UI feel (button spacing, mobile fit of the raw-actions row) — for Les to
  eyeball in the web UI; mechanics are covered by vitest + browser scenario.
