# Notes: Issues 1089 & 1094

## Session Context
- **Date**: 2026-10-21
- **Branch**: `issue-1089-1094-transcript-internal-messages`
- **Issues**:
  - #1089: Re-rendered conversation displays some tool messages and "user" messages oddly
  - #1094: Compaction events don't seem to be visible in the transcript
- **Baseline**: 4639 passed, 2 skipped (pytest); 523 passed (vitest).

## Root Cause Analysis
1. In `src/decafclaw/web/static/components/chat-message.js`, only `vault_retrieval` and `vault_references` were routed to `<tool-message>`. All other auto-injected context roles (`conversation_notes`, `recent_journal`, `workspace_references`, `mcp_references`) and lifecycle roles (`cancel_marker`, `turn_aborted`) fell through to the default `<user-message>` branch.
2. In `src/decafclaw/compaction.py`, compaction completion published `compaction_end` over the EventBus and forwarded it via WebSockets as `WSMessageType.COMPACTION_DONE`, but never appended a record to `archive.jsonl`. Refreshing the browser or switching conversations dropped compaction events from the transcript.
3. In `restore_history()`, metadata messages (`compaction`, `model`, `mode`) were not filtered out from the restored history loaded by the agent runner.

## Changes Made
1. **Backend Persistence & Filtering**:
   - `src/decafclaw/compaction.py`:
     - In `compact_history()` `finally:` block, persist a record with `role: "compaction"`, shared notice text from `compaction_notice()`, and structured metrics (`data`: `before_messages`, `after_messages`, `estimated_tokens_before`, `estimated_tokens_after`, `elapsed_sec`, `success`) to `archive.jsonl`.
     - In `_split_into_turns()` and `flatten_messages()`, ignore metadata roles (`compaction`, `model`, `mode`) so they are excluded from LLM prompts and turn boundary calculations.
   - `src/decafclaw/archive.py`:
     - In `restore_history()`, exclude metadata roles (`compaction`, `model`, `mode`) from the restored message history.
   - `src/decafclaw/web/message_types.json` & `scripts/gen_message_types.py`:
     - Added optional `elapsed_sec`, `estimated_tokens_before`, `estimated_tokens_after` fields to `compaction_done` and regenerated message types across Python, JS, and TS.
   - `src/decafclaw/web/websocket.py`:
     - Forward `elapsed_sec`, `estimated_tokens_before`, `estimated_tokens_after` in `WSMessageType.COMPACTION_DONE`.

2. **Frontend Rendering & Store**:
   - `src/decafclaw/web/static/components/chat-message.js`:
     - Added explicit branches for `conversation_notes` (📝), `recent_journal` (📓), `workspace_references` (📁), and `mcp_references` (🔌) rendering `<tool-message>`.
     - Added explicit branches for `cancel_marker` and `turn_aborted` rendering distinct `.chat-event` notice banners.
     - Enhanced `compaction` rendering to display a card with shared notice text and metrics chips (message delta, token reduction percentage, duration).
   - `src/decafclaw/web/static/styles/chat.css`:
     - Styled `.compaction-notice`, `.compaction-metrics`, `.metric-chip`, and generalized `chat-message .chat-event` with custom borders for `cancel-marker` and `turn-aborted`.
   - `src/decafclaw/web/static/lib/message-store.js`:
     - Handled `compaction` in `MESSAGE_TYPES.CONV_HISTORY` to retain compaction records and forward `data` as `record`.
     - Updated `MESSAGE_TYPES.COMPACTION_DONE` handler to format notice text matching `compaction_display.py` with duration and tokens when available.
   - `src/decafclaw/web/static/lib/conversation-store.js`:
     - Added `data` property to `ChatMessage` JSDoc type.

3. **Tests**:
   - `tests/test_compaction.py`: Added tests for archive persistence on compaction success and failure, and metadata exclusion from `_split_into_turns` and `flatten_messages`.
   - `tests/test_context_composer.py`: Added test verifying archived `compaction` messages are ignored by `ContextComposer`.
   - `src/decafclaw/web/static/components/chat-message.test.js`: Added comprehensive tests for all new role render branches and compaction card with metrics.
   - `src/decafclaw/web/static/lib/message-store.test.js`: Added tests for `COMPACTION_DONE` formatting, `CONV_HISTORY` retention of internal roles and compaction events.

## Test Results
- `make check`: PASSED (npm policy, message types sync, formatting, linting, pyright typecheck, tsc check-js, browser asset graph).
- `make test`: PASSED (4643 passed, 2 skipped).
- `make test-js`: PASSED (48 files, 532 tests).
- `make check-tui` & `make test-tui`: PASSED (46 tests).
- `tests/test_web_browser_scenarios.py`: PASSED (24 tests).
