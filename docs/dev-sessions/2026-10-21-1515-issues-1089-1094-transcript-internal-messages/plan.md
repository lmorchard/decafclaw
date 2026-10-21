# Plan: Issues 1089 & 1094 — Transcript Internal Messages & Compaction Events

## Phase 1: Compaction Persistence & Summarizer Filter (Backend - Issue 1094)
1. Write failing tests in `tests/test_compaction.py` and `tests/test_context_composer.py` asserting:
   - `compact_history()` writes `role: "compaction"` to `archive.jsonl` on success and on failure.
   - `_split_into_turns()` and `flatten_messages()` exclude `role: "compaction"`, `"model"`, `"mode"`.
   - `ContextComposer` ignores `role: "compaction"` in token counting and prompt construction.
2. Implement backend changes in `src/decafclaw/compaction.py`:
   - Call `compaction_notice()` and `append_message()` in `finally:`.
   - Filter metadata roles in `_split_into_turns()` and `flatten_messages()`.
3. Update `src/decafclaw/web/message_types.json` with `elapsed_sec`, `estimated_tokens_before`, `estimated_tokens_after` on `compaction_done` and run `make gen-message-types`.
4. Update `src/decafclaw/web/websocket.py` to forward those fields on `WSMessageType.COMPACTION_DONE`.
5. Run Python tests to verify Phase 1 passes.

## Phase 2: Frontend Message Rendering (Issues 1089 & 1094)
1. Write failing JS unit tests in `src/decafclaw/web/static/components/chat-message.test.js`:
   - Test rendering of `conversation_notes`, `recent_journal`, `workspace_references`, `mcp_references` as `<tool-message>`.
   - Test rendering of `cancel_marker` and `turn_aborted` as `.chat-event`.
   - Test rendering of `compaction` as `.compaction-notice` (with and without detailed metrics).
2. Implement `src/decafclaw/web/static/components/chat-message.js`:
   - Add cases for `conversation_notes`, `recent_journal`, `workspace_references`, `mcp_references`.
   - Add cases for `cancel_marker`, `turn_aborted`.
   - Enhance `compaction` rendering to display formatted notice card with metrics (message delta, token reduction percentage, duration).
3. Update `src/decafclaw/web/static/styles/chat.css`:
   - Add rules for `.chat-event.cancel-marker`, `.chat-event.turn-aborted`, and enhanced `.compaction-notice`.
4. Update `src/decafclaw/web/static/components/chat-view.js`:
   - Forward `record: m.record || m.data || null` to `<chat-message>`.
5. Run JS tests to verify Phase 2 passes.

## Phase 3: Message Store & History Replay (Issues 1089 & 1094)
1. Write failing JS unit tests in `src/decafclaw/web/static/lib/message-store.test.js`:
   - History replay preserving `compaction` messages with metadata.
   - History replay preserving internal context roles and pairing tool calls with tool results.
   - Live `COMPACTION_DONE` formatting matching `compaction_display.py` with tokens and duration.
2. Implement changes in `src/decafclaw/web/static/lib/message-store.js`:
   - Update `COMPACTION_DONE` handler to format notice with duration, message counts, and token estimates matching `compaction_display.py`.
   - Ensure `MESSAGE_TYPES.CONV_HISTORY` preserves `compaction` records and attaches `record: m.data || m.record`.
3. Run JS tests to verify Phase 3 passes.

## Phase 4: Full Validation & Documentation
1. Run full test suites:
   - `make check` (npm policy, message types, formatting, linting, typecheck, check-js, browser assets).
   - `make test` (pytest suite).
   - `make test-js` (vitest suite).
2. Update documentation if necessary (`docs/conversations.md`).
3. Commit logical changes and document findings in `notes.md`.
