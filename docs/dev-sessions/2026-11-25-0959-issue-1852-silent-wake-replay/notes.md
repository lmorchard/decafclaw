# issue-1852-silent-wake-replay

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1852
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1852-silent-wake-replay
- Branch: issue-1852-silent-wake-replay
- Base: origin/main at 8b0e9667
- Next action: rerun gates, commit, publish draft, self-review, mark ready.

## Decisions

- Persist `suppress_user_message: true` on the final assistant row and strip
  the leading sentinel from its archived content. The live `ToolResult` keeps
  the raw sentinel, so the existing manager/transport suppression is unchanged.
- `sentinels.py` now owns the start-anchored matcher (`response_starts_with_sentinel`,
  `strip_leading_sentinel`) so archive/replay code can use it without importing
  the heartbeat runtime. `heartbeat.py` imports it; existing imports keep working.
- `wake_replay.py` normalizes transcript rows: the persisted flag is
  authoritative. Legacy rows are stripped only inside a structurally identified
  wake turn (`wake_trigger` opens it, `user` closes it), never by bare text
  presence outside a wake.
- `read_archive_file` normalizes on read, covering web history, restore/thread
  fork, compaction, export, forks, and auto-titling in one place.
- Provider history strips the archive-only `suppress_user_message` key.
- Web replay renders a collapsed `◇ wake · silent` row, expandable to the
  stripped body; empty-body rows still render. Export renders the same row.
- Compaction skips `wake_trigger` rows (their prompt contains the literal
  sentinel) and labels an empty silent wake as `[silent wake]`.
- Mattermost has no transcript re-render; its replay input is restored
  thread-fork history, covered by the archive normalization.

## Evidence

- `uv run pytest tests/test_wake_replay.py tests/test_agent_turn.py tests/test_compaction.py tests/test_conversation_export.py tests/test_heartbeat.py tests/test_restore_history.py tests/test_background_wake_integration.py tests/test_conversation_manager.py -q -n 0` → 262 passed.
- `make test-js` → 919 passed (73 files).
- `make lint`, `make typecheck` → clean.
- `make check` → passed; `make test` → 7073 passed, 3 skipped, only the four
  pre-existing `tests/test_host_metrics.py` host failures.
