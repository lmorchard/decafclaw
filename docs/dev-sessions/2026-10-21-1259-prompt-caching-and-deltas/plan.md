# Plan: Prompt Caching Prefix Stability and Delta Context Injections

- Issues: #1111, #1116
- Worktree: `issue-1111-1116-prompt-caching-and-deltas`

## Phase 1: High-Water Marks & Delta Injections (#1111)
- [ ] Add `reset_for_compaction()` method on `ComposerState` (`injected_paths.clear()`, `cleanup_cleared_count = 0`, `cleanup_cleared_bytes = 0`, `last_recent_journal_ts = None`, `last_notes_count = 0`).
- [ ] Add `last_recent_journal_ts` and `last_notes_count` to `ComposerState`, `PersistedTurnState`, `_PERSISTED_BINDINGS`, and `_CTX_DRIVEN_FIELDS`.
- [ ] Update `compaction.py` (and `agent.py`) to call `reset_for_compaction()` on `ctx.composer` when compaction succeeds.
- [ ] Update `_compose_notes` to inject only deltas when `last_notes_count > 0`, updating `ctx.composer.last_notes_count`.
- [ ] Verify `_compose_recent_journal` already updates `ctx.composer.last_recent_journal_ts` and ensure tests verify delta and multi-turn persistence.
- [ ] Write unit tests for notes delta injection, journal persistence across turns in `ConversationManager`, and compaction resets.

## Phase 2: Prefix Stability for Prompt Caching (#1116)
- [ ] Change `status_line` (`show_context_status`) role from `system` to `user` so it does not get pulled into Vertex `systemInstruction` or break the system prompt prefix.
- [ ] Move `preempt_skill_text` from the system message prefix to the turn's injected message stream (e.g. role `preempt_skill_hint` remapped to `user`, archived like other injections, placed before user message).
- [ ] Disable routine per-turn in-place `clear_old_tool_results` in `agent.py:_maybe_compact` (or run only during compaction) so past message bodies are not rewritten in-place.
- [ ] Add a comprehensive unit test proving that between compactions across turns 1..3, turn N's messages prefix is a strict prefix of turn N+1's messages prefix.

## Phase 3: Documentation & Verification
- [ ] Update `docs/context-composer.md` documenting append-only prefix invariants, notes/journal delta injections, and compaction resets.
- [ ] Run full test suite: `make test`, `make check`.
- [ ] Commit with clean git history and prepare PR.
