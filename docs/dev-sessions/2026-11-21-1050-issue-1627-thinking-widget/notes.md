# issue-1627-thinking-widget

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1627
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1627-thinking-widget
- Branch: issue-1627-thinking-widget
- Base: origin/main at 9c02ef43efb46f7a9b7106e9f216627d000dc937
- Next action: commit and submit PR (implementation verified).

## Log

- Root cause: history reload merged assistant+tool_calls rows into tool rows and dropped `thinking`; the empty assistant row was then suppressed, so a thinking-only pre-tool iteration vanished. Live streaming looked right because `MESSAGE_COMPLETE` carried `thinking`.
- Design (per issue): thinking is its own transcript row (`role: 'thinking'`, `thinking-message` component), separate from the assistant reply and tool rows. Wire format unchanged; the client splits `assistant.thinking`.
- `MessageStore.#splitThinking` runs on `CONV_HISTORY` rows and `MESSAGE_COMPLETE`; the thought row takes `provenance`/`recalled_facts` so capsules stay on the first element of the turn.
- `assistant-message` and `tool-call-message` no longer render thought blocks; `ChatView` streams thinking and answer as separate widgets.
- Tests: message-store split cases, chat-view streaming/history cases, new thinking-message tests, tool-call/assistant rewrites, and a Playwright history-reload scenario.
- Evidence:
  - `npx vitest run`: 844 passed.
  - `make test`: 6132 passed, 2 skipped.
  - `make test-browser`: 35 passed.
  - `make check`: green (after restoring generated openapi/api-client drift).
  - Live `make run-dev` + Chromium: seeded thinking-only tool-call conversation; thought row present after first load, after switching to another conversation and back, and expands to show text.
