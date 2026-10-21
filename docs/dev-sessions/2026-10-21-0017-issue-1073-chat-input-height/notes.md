# issue-1073-chat-input-height

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1073 (textarea shorter than flanking buttons)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1073-chat-input-height
- Branch: issue-1073-chat-input-height
- Base: origin/main at f200a4f0c71424036cd864c7d0393845f4257ef7
- Status: implemented + tested; all local gates green; commit + draft PR pending.

## Problem and fix

`.input-row` is `display: flex; align-items: stretch`, so with the textarea at
natural (`auto`) height, stretch equalizes it with the Pico buttons. But
`#handleInput` sets an **explicit** height on every input event, which opts the
textarea out of stretch — a single line of text left it at its content height
(~2.5rem) while the buttons are ~3.125rem, i.e. visibly shorter (#1073).

Fix (`src/decafclaw/web/static/components/chat-input.js`, `#handleInput`):
floor the auto-resized height at the row's height while the textarea is at its
natural size. The floor can never exceed 128px because the textarea's CSS
`max-height: 8rem` caps its natural contribution to the row, so the existing
128px auto-resize cap is preserved for long text.

## Checks

- `make fmt`: clean (441 files unchanged), 2026-10-21 ~05:35 PDT.
- `tests/test_web_browser_scenarios.py::test_browser_chat_input_height_alignment`
  (new): PASSED with the fix; FAILED without it (assertion at
  "after single-line input") — verified via temporary `git stash` of the JS
  change. The test asserts row/textarea/button border-box heights agree within
  0.5px after initial render, single-line input, and clearing.
- `make check`: all sub-gates passed (check-npm-policy, check-message-types,
  fmt-check, lint, pyright 0 errors, check-js/tsc, static module graph).
- `make check-browser-assets`: exit 0.
- `make test` (full suite): 4629 passed, 2 skipped in 44.42s.

## Log

- 05:2x resumed from prior worker (died at context limit before checks/commit):
  diff intact, base confirmed at f200a4f0, issue In progress on board.
- 05:35 fmt clean; new browser test green; negative check green (test fails
  without fix); `make check` green.
- 05:4x `make test` full suite: 4629 passed, 2 skipped in 44.42s.
