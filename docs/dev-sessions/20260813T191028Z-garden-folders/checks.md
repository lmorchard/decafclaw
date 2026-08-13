# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/193
**Frozen at:** d9a2e1fb
**Check files — read-only from Phase 1 onward:**
- `tests/test_garden_folders.py`

## C1
CRITERION: WHEN the garden maintenance sweep runs and detects 3+ agent pages sharing a common topic cluster at the vault root (or any folder), THE GARDEN SKILL SHALL execute moving those pages into a dedicated subdirectory under `agent/pages/` (unless `dry_run` is enabled via configuration).
CHECK: `pytest tests/test_garden_folders.py::test_garden_detects_and_suggests_cluster_folder_moves` passes.
AT FREEZE: fails - `AssertionError: test_garden_detects_and_suggests_cluster_folder_moves is not implemented yet`

## C2
CRITERION: WHEN pages are moved into a folder during garden reorganization, THE VAULT SYSTEM SHALL update existing `[[wiki-links]]` pointing to those pages (or correctly resolve them via stem-based resolution).
CHECK: `pytest tests/test_garden_folders.py::test_garden_folder_move_updates_links` passes.
AT FREEZE: fails - `AssertionError: test_garden_folder_move_updates_links is not implemented yet`

## C3
CRITERION: GIVEN `dry_run` configuration is enabled WHEN garden runs page reorganization THEN it SHALL log or report proposed moves without modifying files on disk or flattening user-created folder hierarchies.
CHECK: `pytest tests/test_garden_folders.py::test_garden_folder_move_dry_run_and_respect_user_folders` passes.
AT FREEZE: fails - `AssertionError: test_garden_folder_move_dry_run_and_respect_user_folders is not implemented yet`

## Guards

- G1: `pytest tests/test_vault_tools.py` passes — existing vault operations remain fully functional.
- G2: `pytest tests/test_recompute_importance.py` passes — existing garden operations (like importance recompute) remain fully functional.

## Adjudication

- C1: accepted - the check requires the test to pass, which will only happen once the logic is implemented
- C2: accepted - the check correctly validates wiki-links update upon move
- C3: accepted - the check correctly ensures dry_run prevents writing files
- G1: accepted - guard protects vault tools
- G2: accepted - guard protects garden recompute importance

## Amendments

1. Clarification: G2 command was updated from `tests/test_garden_recompute.py` to `tests/test_recompute_importance.py` to fix a typo in the file name. This does not change the criteria or tier.
