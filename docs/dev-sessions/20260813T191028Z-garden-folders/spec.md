## Context

Follow-up from #170 (vault folder support). Once folders are available, the garden skill should be able to suggest or execute page moves into folders during its periodic maintenance sweeps.

## Idea

During garden maintenance:
- Detect clusters of related pages at the vault root (or in any folder)
- When 3+ pages share a clear topic, suggest consolidating them into a folder
- Could be fully automatic or produce a "proposed reorganization" for user review
- Should update [[wiki-links]] if pages move (or rely on stem-based resolution to handle it)

## Considerations

- Need to be conservative — dont break existing links or surprise users
- May want a dry-run mode that logs proposed moves without executing
- Should respect any user-created folder structure (dont flatten what the user organized)
- Embedding re-indexing happens automatically on rename (implemented in #170)

## Related

- #170 — vault folder support (parent feature)

## Verifiable acceptance criteria

- CRITERION: WHEN the garden maintenance sweep runs and detects 3+ agent pages sharing a common topic cluster at the vault root (or any folder), THE GARDEN SKILL SHALL execute moving those pages into a dedicated subdirectory under `agent/pages/` (unless `dry_run` is enabled via configuration).
  - CHECK: `pytest tests/test_garden_folders.py::test_garden_detects_and_suggests_cluster_folder_moves` passes.
- CRITERION: WHEN pages are moved into a folder during garden reorganization, THE VAULT SYSTEM SHALL update existing `[[wiki-links]]` pointing to those pages (or correctly resolve them via stem-based resolution).
  - CHECK: `pytest tests/test_garden_folders.py::test_garden_folder_move_updates_links` passes.
- CRITERION: GIVEN `dry_run` configuration is enabled WHEN garden runs page reorganization THEN it SHALL log or report proposed moves without modifying files on disk or flattening user-created folder hierarchies.
  - CHECK: `pytest tests/test_garden_folders.py::test_garden_folder_move_dry_run_and_respect_user_folders` passes.

## Regression guards

- GUARD: `pytest tests/test_vault_tools.py tests/test_garden_recompute.py` passes — existing vault operations and importance recompute remain fully functional.

## Tier: auto-ok

Reason: All acceptance criteria have concrete runnable tests, and the design decision regarding default behavior (`dry_run=False` by default with config support to enable `dry_run`) has been approved by the repo owner.

## Design decisions

- **Decision:** Folder reorganization executes by default (`dry_run=False`), with a configuration setting (`garden.dry_run = True`) available to enable dry-run mode.
  - **Why:** Aligned with owner feedback ("let's not make this dry_run=True by default. Let's support a configuration setting to disable, but enable by default").
  - **Rejected:** Dry-run by default requiring explicit opt-in.