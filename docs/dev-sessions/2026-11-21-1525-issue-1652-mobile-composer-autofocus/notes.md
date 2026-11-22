# issue-1652-mobile-composer-autofocus

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1652
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1652-mobile-composer-autofocus
- Branch: issue-1652-mobile-composer-autofocus
- Base: origin/main at 1da41bd9144eca953c2523d15469fc687f9cfe89
- Status: implementation + tests + docs complete; live-verified desktop/mobile; ready to commit.
- Next action: commit, then continue to draft/ready PR per ghflow express-issue.

## Decisions

- Mobile signal = `(pointer: coarse)`, not the 639px breakpoint. A landscape
  phone is wider than the breakpoint but its soft keyboard still covers the
  transcript; a narrow desktop window keeps its hardware keyboard and can
  autofocus. Documented in docs/web-ui.md and docs/web-ui-mobile.md.
- Policy lives in `lib/chat-focus.js`: `isMobileKeyboardDevice()` and a new
  `isMobile` option on `shouldFocusChatInput` that short-circuits before
  conv-switch/turn-finish focusing. `app.js` supplies it from the store
  `change` handler. Cmd+/ and explicit taps still focus.
- Automatic focus paths affected: initial load (first conv id), conversation
  switch, and busy→idle turn end — all share that one handler.

## Checks

- Baseline (pre-change): `make test-js` 863 passed; `make test` 6149 passed,
  2 skipped.
- New test fails before fix (mobile conv-switch returned true).
- `npx vitest run lib/chat-focus.test.js`: 13 passed.
- `make check`: passed; no generated-file drift (`git status` clean for
  openapi/api-client).
- Live (headless Chromium, `make run-dev` on 18991):
  - Desktop (coarse=false): Ctrl+K new conversation → textarea focused.
  - Pixel 5 emulation (coarse=true, 393px): Ctrl+K new conversation → BODY
    active, nothing in chat-input; reload of an existing conv also left BODY.
  - Mobile explicit tap on `chat-input textarea` → textarea focused.
  - Temp login token created for the check was revoked; dev server stopped.

## Limits

- Turn-finished focus was not exercised live (no model turn run); the shared
  handler and unit test cover it.
