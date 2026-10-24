# #1160 — durable per-conversation shell approval state

## Resume here
- Branch `issue-1160-approval-state-durability`, worktree `.claude/worktrees/issue-1160-approval-state-durability`, base origin/main 3bc8afad.
- Implementation complete; see PR for status.

## Decisions
- Confirmed by Les (issue comment 6377413379): sidecar file, no persisted reviewer cache, scope = guidance + preset lists + active_mode.
- **Deviation from decision 1 (location):** the file lives at `data/{agent_id}/approval_state/{conv_id}.json`
  (`conversation_paths.approval_state_path`), not `workspace/conversations/{conv_id}/approval_state.json`.
  Reason: `workspace/` is writable by the agent's `workspace_write` (`_resolve_safe` only checks containment in the
  workspace), and `aux_approval_guidance` is fed to the shell reviewer — a workspace file would let the agent grant
  itself shell auto-approval after a restart. Same reasoning as scheduled wakes (#1119). Flipping back is a one-line
  change in `approval_state_path`; deletion is handled explicitly in `delete_conversation_files`.
- `ApprovalState` dataclass fields share names with `PersistedTurnState`; transfer iterates `fields(ApprovalState)`.
- Load in `ConversationManager._get_or_create`; write via `_persist_approval_state` at the end of
  `_save_conversation_state` and in `set_flag` for approval fields. Skip when equal to last-saved snapshot
  (`ConversationState.approval_state_saved`) or when no sidecar exists and state is default.
- Websocket history load uses new `manager.ensure_state` so the sidecar is loaded before mode is reported. Legacy
  archived-mode fallback gated on `approval_state_saved is None`; it now goes through `set_flag`, so it migrates once.
- agent.py's archive mode scan left in place (legacy fallback, commented).

## Evidence
- Baseline `make test` (pytest tests/ contrib/skills/): 4786 passed, 2 skipped.
- New tests: tests/test_approval_state.py, 2 new tests in tests/test_websocket_modes.py.
- Mutation checks (each disabled behavior fails at least one new test): no load, no turn-end persist, no set_flag
  persist, no skip-unchanged, no legacy gate, no delete, no fail-open — all caught.
