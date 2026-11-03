# issue-1190-move-floating-buttons

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1190
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1190-move-floating-buttons
- Branch: issue-1190-move-floating-buttons
- Base: origin/main at 2401a1fc93648865627a8f097f31257e96aa652d
- Endpoint: through PR review follow-up, no merge. Attended. Same-model review waived by user (this session).
- Next action: self-review the in-progress implementation, then commit, push, and publish the draft PR.

## Log

- 2026-11-03: Worktree created from origin/main @ 2401a1fc; branch + notes + board In progress confirmed. Baseline JS tests (chat-controls, chat-input, side-question-drawer, chat-view: 51 passed) and module-graph pytest (2 passed) green before implementation.
- 2026-11-03: Implementation started (issue #1190): move four floating chat action buttons into `<chat-controls>` control bar; dedupe side-question `?` to the control bar entry. Design: ChatControls renders the action cluster in its lit template (canvas pill via canvas-state subscription, copy-conversation-menu Lit element, terminal via store.sendMessage('/terminal'), side-question via open-side-question dispatch); context control keeps `margin-left:auto` (right end); cluster sits between mode control and context control. Chat-input input-row side-question button to be removed.
- 2026-11-03: Implementation completed in-session (no subagents): floating cluster removed from app.js + chat.css; action cluster added to chat-controls.js + chat-controls.css; side-question button + handler removed from chat-input.js; chat-input.test.js updated. Checks: vitest 596/596 passed, tsc --noEmit clean, module-graph pytest 2 passed, ruff fmt/check clean. Docs updated (web-ui.md, web-ui-design.md, web-ui-mobile.md).

## Worker returns

(none)
