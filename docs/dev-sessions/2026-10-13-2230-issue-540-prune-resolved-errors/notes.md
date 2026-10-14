# Issue #540 — prune resolved errors from tool-result history

- Issue: https://github.com/lmorchard/decafclaw/issues/540
- Branch: `issue-540-prune-resolved-errors` (from `origin/main @ e3d31836`)
- Worktree: `.claude/worktrees/issue-540-prune-resolved-errors`
- Base CI: `e3d31836` green (lint-and-test / js-test / check-tui)
- Baseline `make test` (worktree, pre-change): **4536 passed, 2 skipped** (18.52s)

## Binding spec (issue spec comment 2026-08-18, Les 👍)

Add a second pass to `clear_old_tool_results` (`context_cleanup.py`): when a tool
result is an error AND a later call to the **same tool** succeeds, stub the earlier
error in the in-memory view (STUB, not drop) — more aggressive than the size/age
clear. Originals stay in the JSONL archive (same invariant as the existing clear tier).

- Matching: same tool name by default; `prune_resolved_same_args_only` = conservative
  same-tool+same-args tunable.
- Config: `CleanupConfig` gains `prune_resolved_errors: bool = True` and
  `prune_resolved_same_args_only: bool = False`.
- Tests (named oracles, `tests/test_context_cleanup.py`):
  `test_prunes_resolved_error_results`, `test_prune_resolved_errors_config`.
  Existing suite must keep passing.
- Modes: skip heartbeat/scheduled/child — "same as memory retrieval".
- Out of scope: compaction decision-slice, evals.

## Decisions taken unattended (record in PR; do NOT re-decide)

- **`prune_resolved_errors` default = True.** Evidence: issue body example config
  shows `prune_resolved_errors: true`; approved spec criterion phrased
  "GIVEN cleanup.prune_resolved_errors = true". Tradeoff: in-memory-only (archive
  intact), same class as the default-enabled clear tier; disable via
  `cleanup.prune_resolved_errors = false`.

## Implementation notes / findings

- Error idiom: tool errors land as tool-role messages whose `content` starts with
  `[error` (`ToolResult(text="[error: ...]")`, see `agent.py:211`
  `_extract_call_signatures` uses `content.lstrip().startswith("[error")`). Use
  `content.lstrip().startswith("[error")`.
- Mode signal: `ctx.is_child` (bool) + `ctx.task_mode` (str) → `ComposerMode`,
  mapped in `agent.py:_compose`. Memory retrieval skips
  `{HEARTBEAT, SCHEDULED, CHILD_AGENT}` (`context_composer.py:804` etc.).
  The existing clear tier runs in ALL modes (no mode gate) — the new pruning gets
  a mode gate; the size-based clear is left unchanged (still all modes).
- `context_composer` does not import `context_cleanup` or `agent`, so importing
  `ComposerMode` into `context_cleanup.py` is cycle-safe (only `agent.py` imports
  `context_cleanup`).
- `load_sub_config` auto-derives env vars from field names → new fields get
  `CLEANUP_PRUNE_RESOLVED_ERRORS` / `CLEANUP_PRUNE_RESOLVED_SAME_ARGS_ONLY` for
  free; no manual env mapping needed.
- Pruning runs BEFORE the size/age guards (independent of `min_turn_age` /
  `min_size_bytes`) and folds reclaimed bytes into the existing `ClearStats`.
- Stub text: `[tool error superseded by later success]`.
- Guard: a resolved error smaller than the stub is left as-is (stubbing would
  make the in-memory view larger) — mirrors the size/age clear's pathological
  `reclaimed <= 0` skip. `test_tiny_resolved_error_not_pruned` locks it in.

## Status

- [x] Worktree + venv + `.env` (HTTP_PORT=18990) + baseline (4536 passed, 2 skipped)
- [x] Config fields (`config_types.py`): `prune_resolved_errors`, `prune_resolved_same_args_only`
- [x] Pruning pass (`context_cleanup.py`): `_is_error_result`, `_build_tool_args_index`,
      `_prune_resolved_errors`; `mode` param on `clear_old_tool_results`; pruning runs
      before size/age guards, folds into `ClearStats`
- [x] Mode derivation shared helper `_composer_mode_for` in `agent.py`; used by `_compose`
      and passed at the `clear_old_tool_results` call site in `_maybe_compact`
- [x] Tests: `TestPruneResolvedErrors` (8 tests incl. named oracles
      `test_prunes_resolved_error_results`, `test_prune_resolved_errors_config`)
- [x] Docs (`context-composer.md`, `config.md`)
- [x] `make fmt` (ruff joined signature to one line; line-length=120), `make check` green,
      `make test` 4544 passed / 2 skipped
- [x] Commit + draft PR

## Gate results (this tree)

- `make check`: fmt-check (432 formatted), lint (all pass), pyright (0/0/0),
  check-message-types (no diff), tsc (clean), module-graph (2 passed).
- `make test`: **4544 passed, 2 skipped** (baseline 4536 → +8 new tests).

## Handoff

- Draft PR: https://github.com/lmorchard/decafclaw/pull/1071 (draft, not ready,
  no review requested).
- Head: the branch `issue-540-prune-resolved-errors` tip — a `docs(context)`
  commit on top of `feat(context)` core `0e8b38b1`; base `origin/main @ e3d31836`.
  The PR head is the authoritative SHA (this notes file is committed in that
  docs commit, so it deliberately does not pin its own SHA).
- Hosted CI on head `aef13576`: green (required `lint-and-test` + `js-test`
  passed; `check-tui` passed).
- Board: #540 at In progress (already there on arrival; a draft does not
  qualify for In review).
- Next (coordinator): independent fresh-context review of the published head.
