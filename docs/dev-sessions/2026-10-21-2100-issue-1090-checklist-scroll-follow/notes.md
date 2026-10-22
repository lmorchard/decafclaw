# issue-1090-checklist-scroll-follow

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1090
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1090-checklist-scroll-follow
- Branch: issue-1090-checklist-scroll-follow
- Base: origin/main at c7786768f0b9215501e73d1efb97055b53666111
- Next action: commit and submit draft PR.

## Log

- Root cause: when checklist widget appeared in `<sticky-slot>`, `<chat-view>` clientHeight shrank, causing scroll distance to bottom to exceed 100px. Autoscroll stopped and "New messages" button appeared.
- Fix: Add ResizeObserver to `<chat-view>` to re-anchor scroll to bottom when resized while following new messages (`!this._showScrollBtn`), ensuring smooth autoscroll across layout changes (sticky slot expand/collapse, window resize, keyboard shifts).
- Tests added: `src/decafclaw/web/static/components/chat-view.test.js` covering ResizeObserver connect/disconnect, scroll adjustment on resize when following, and non-adjustment when user scrolled up.
- Verified: `make check-js`, `make test-js` (536 passed), `make check` (all clean).
