# feat-1141-side-questions

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1141
- Worktree: /home/lmorchard/devel/decafclaw/.claude/worktrees/feat-1141-side-questions
- Branch: feat-1141-side-questions
- Base: origin/main at 875f2b7ab70a001000b0f8d67814d57ed8a91dd2
- Next action: run all gates and publish PR.

## Log

- 2026-10-26: Created worktree from origin/main at 875f2b7ab70a001000b0f8d67814d57ed8a91dd2.
- Baseline checks verified (`make check`, `make test-js`, `make test` all passing).
- Implemented `ConversationManager.run_side_question` and `cancel_side_question`:
  - Snapshots current conversation messages up to the current turn (including partial assistant text if streaming).
  - Formats messages and strips all tools (`tools=None`, no `tool_calls` parameter sent to model).
  - Uses an isolated `Context` and `SIDE_QUESTION_SYSTEM_PROMPT`.
  - Independent `asyncio.Event` cancellation so cancelling a side question does not affect the main task, and vice versa.
  - Does not mutate `state.busy` or turn queue.
- Added `POST /api/conversations/{id}/side-question` (SSE streaming) and `POST /api/conversations/{id}/side-question/cancel`.
- Implemented `SideQuestionDrawer` component (`side-question-drawer.js` and `side-question-drawer.css`):
  - Out-of-band slide-out drawer with dedicated question input and ephemeral thread.
  - Streaming tokens via SSE.
  - "Send to main conversation" action copying explanation into `chat-input` without auto-submitting.
  - Added "Ask side question" action triggers in composer and `.dc-chat-actions`.
- Added unit tests in `tests/test_side_questions.py` and `side-question-drawer.test.js`.
- Updated documentation in `docs/web-ui.md` and `docs/conversations.md`.
