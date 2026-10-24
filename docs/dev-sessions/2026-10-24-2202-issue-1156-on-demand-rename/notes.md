# issue-1156-on-demand-rename

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1156
- Worktree: /home/lmorchard/devel/decafclaw/.claude/worktrees/issue-1156-on-demand-rename
- Branch: issue-1156-on-demand-rename
- Base: origin/main at 0b54f372cf311425808c55cc3d6fda80199751bf
- Implementation and verification complete.

## Changes

- **`ConversationIndex.rename` (`src/decafclaw/web/conversations.py`)**:
  - Added `clear_custom: bool = False` keyword argument.
  - When `clear_custom=True`, overrides any existing `custom_title` and clears the flag (popping it from dictionary), returning the conversation to automatic periodic refinement.
- **`retitle_conversation` (`src/decafclaw/web/auto_titling.py`)**:
  - Bypasses cadence (`TITLING_TURNS`) and dedup/exhaustion guards.
  - Generates title via `_generate_title` using recent user/assistant turns.
  - Renames in `ConversationIndex` with `manual=False` and `clear_custom=True`.
  - Publishes `conv_renamed` event on `event_bus` to notify all connected tabs.
  - Fails open: returns `(False, err)` on error or insufficient history without mutating title or raising.
- **WebSocket `/retitle` command (`src/decafclaw/web/websocket.py`)**:
  - Intercepts `/retitle` and `!retitle` as server-side side-effect commands in `_handle_send` before agent turn dispatch (no agent turn, no archive write, like `/terminal`).
  - Checks conversation read authorization.
  - Calls `retitle_conversation`. On success, is chat-silent (updates via `conv_renamed`). On error, sends `WSMessageType.ERROR` so client shows a toast.
- **Client integration (`conversation-store.js` & `conversation-sidebar.js`)**:
  - `conversation-store.js`: `sendMessage` bypasses busy flag and user message push for `/retitle` and `!retitle`. Added `retitleConversation(convId)`.
  - `conversation-sidebar.js`: Added "Regenerate title" (✨) action button (`onAction2`) on active conversation rows invoking `store.retitleConversation(conv_id)`.
- **Documentation (`docs/web-ui.md`)**:
  - Documented on-demand retitling via `/retitle` command and sidebar "Regenerate title" action.
- **Tests**:
  - Python tests: `tests/test_web_auto_titling.py` (success, bypass cadence/exhaustion, override/clear custom_title, fail-open, empty history) and `tests/web/test_retitle_command.py` (chat-silent, no turn, no archive, unauthorized error, empty history error, command boundary guard).
  - JS tests: `conversation-sidebar.test.js` (button render and click dispatch), `conversation-store.test.js` (sendMessage `/retitle` bypass and `retitleConversation`), and `sidebar-row-a11y.test.js` (row button labels).
