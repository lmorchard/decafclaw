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

## Review Feedback & Fixes (Copilot Review Round 1 & Round 2)
1. **Filtering metadata before compaction boundaries**:
   - In `compaction.py:compact_history()`, filtered `read_archive()` results immediately so `_split_into_turns()` and `_determine_compaction_mode()` operate on an archive without `compaction`, `model`, or `mode` records.
   - Prevents `prev_old_msg_count` from drifting when compaction notices accumulate in the archive across repeated compactions.
   - In `_determine_compaction_mode()`, calculated `prev_recent_count` as `sum(len(turn) for turn in compacted_turns) - protected_msg_count` using filtered turns, so legacy sidecars with metadata records do not cause a miscalculation.
   - Added regression tests `test_repeated_compaction_with_accumulated_compaction_notices` and `test_incremental_skips_with_legacy_metadata_in_sidecar`.
2. **Whole-history token estimates for reduction percentage**:
   - Computed `history_tokens_before = estimate_tokens(flatten_messages(history))` before compaction starts, while keeping `estimated` as the summarizer input tokens.
   - Forwarded `history_tokens_before` and `history_tokens_after` through `compaction_end`, `archive.jsonl`, `WSMessageType.COMPACTION_DONE`, and `message-store.js`.
   - In `chat-message.js`, computed `tokenReduction` using `rec.history_tokens_before ?? rec.estimated_tokens_before` and `rec.history_tokens_after ?? rec.estimated_tokens_after`, ensuring the percentage reflects actual whole-history shrinkage.
   - Added unit tests in `chat-message.test.js`.

## Test Results
- `make check`: PASSED.
- `make test`: PASSED (4645 passed, 2 skipped).
- `make test-js`: PASSED (48 files, 533 tests).
- `make check-tui` & `make test-tui`: PASSED (46 tests).
