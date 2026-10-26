# feat-1141-side-questions

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1141
- Worktree: /home/lmorchard/devel/decafclaw/.claude/worktrees/feat-1141-side-questions
- Branch: feat-1141-side-questions
- Base: origin/main at 875f2b7ab70a001000b0f8d67814d57ed8a91dd2
- Next action: address-pr-review complete, publish final commit.

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
- Published first draft PR #1178 at commit `65c8009abd2b2c5cb614a5767d5aa4d18d2639c2`.
- Addressed review findings (Finding 1, 2, 3, 4, Note 1):
  - Finding 1: Gated partial streaming snapshot on `state.busy` and cleared `partial_assistant_chunks` in turn finally block.
  - Finding 2: Stopped in-flight generation when switching conversation in `SideQuestionDrawer`.
  - Finding 3: Validated request body is a JSON object.
  - Finding 4: Updated SSE event parser to check `data.type || currentEvent` so done events are honored on all chunk boundaries.
  - Note 1: Added `id: str` parameter to route handlers, regenerated OpenAPI client, and added test for `/cancel` endpoint.
- Addressed Copilot review findings:
  - Finding 1: Tracked `current_iteration_assistant_chunks` separately to only append uncommitted active streaming chunks, clearing on tool calls and turn completion.
  - Finding 2: Preserved user attachment metadata and applied `resolve_attachments()` to user messages in snapshot.
  - Finding 3: Validated request payload using `SideQuestionRequest(**body)` Pydantic schema in `handle_side_question`.
  - Finding 4: Added request-local ID tracking and thread isolation in `SideQuestionDrawer` to avoid cross-conversation clobbering.
  - Finding 5: Passed captured conversation ID to `stopGeneration()` on conversation switch.
  - Finding 6: Disabled Clear button while generating in `SideQuestionDrawer`.
- Addressed Round 3 review findings:
  - Do not clear `current_iteration_assistant_chunks` at `tool_call_start`: keep chunks until `note_partial_assistant_archived()` commits the assistant message so streaming text remains visible during tool execution.
  - Added `test_side_question_retains_streaming_text_during_tool_calls_prior_to_archive` in `tests/test_side_questions.py`.
