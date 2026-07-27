# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/656
**Frozen at:** c49752e (2026-07-27)
**Check files — read-only from Phase 1 onward:**
- `tests/test_project_tools.py`

## C1

CRITERION: WHEN `project_switch` changes the active project while the outgoing project is EXECUTING,
THE SYSTEM SHALL clear the `progress_tracker` sticky slot.
CHECK: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_switch_away_from_executing_clears` passes.
AT FREEZE: FAILED — `AssertionError: assert 0 >= 1` where 0 = clear_mock.await_count (correct reason: clear_sticky never called when switching away from EXECUTING project)

## C2

CRITERION: WHEN `project_create` makes a new project active while the outgoing project is EXECUTING,
THE SYSTEM SHALL clear the `progress_tracker` sticky slot.
CHECK: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_create_while_executing_clears` passes.
AT FREEZE: FAILED — `AssertionError: assert 0 >= 1` where 0 = clear_mock.await_count (correct reason: clear_sticky never called when creating new project while current is EXECUTING)

## Guards

(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- G1a: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_done_clears_sticky` — existing clear path on DONE. Passed at freeze (1 passed in 1.73s).
- G1b: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_advance_out_of_executing_clears` — existing clear path on advance out of EXECUTING. Passed at freeze (1 passed in 1.73s).
- G2: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_update_step_emits_during_executing` — tracker must still be SET during EXECUTING. Passed at freeze (1 passed in 1.73s).

## Tamper verdict (pre-squash)

**Recorded at:** acd2d51 (2026-07-27, pre-squash)
**Command:** `git diff c49752e -- tests/test_project_tools.py`
**Result:** CLEAN — empty diff

## Amendments

(Append-only. Empty unless an amendment was made.)
