# Plan

## Phase 0: Freeze acceptance checks

- [x] write `checks.md` with criteria from the spec.
- [x] author tests `tests/test_garden_folders.py`.
- [x] run tests to observe failure.
- [x] review tests, adjudicate, and record freeze sha.

## Phase 1: Define configuration and models

Advances: C1, C3

1. [x] Define `GardenConfig` dataclass in `src/decafclaw/skills/garden/tools.py` with `dry_run: bool = False`.
2. [x] Add the `skill_config` handling to tools.py so `tool_vault_reorganize_folders` can access it.
3. [x] Update `init` in `src/decafclaw/skills/garden/tools.py` if necessary to register the config.

- [x] Check: `pytest tests/test_garden_folders.py::test_garden_folder_move_dry_run_and_respect_user_folders` passes (partially, depends on Phase 2).

## Phase 2: Implement Folder Reorganization Tool

Advances: C1, C3

1. [x] Create a new async tool `tool_vault_reorganize_folders` in `src/decafclaw/skills/garden/tools.py`.
2. [x] Implement clustering logic.
3. [x] Determine target folder `agent/pages/{cluster_topic}/`.
4. [x] Check if it's `dry_run`. If `True`, just log/return planned moves. If `False`, move the files using `Path.rename()`.
5. [x] Expose this tool in `TOOL_DEFINITIONS`.

- [x] Check: `pytest tests/test_garden_folders.py::test_garden_detects_and_suggests_cluster_folder_moves` passes.
- [x] Check: `pytest tests/test_garden_folders.py::test_garden_folder_move_dry_run_and_respect_user_folders` passes.

## Phase 3: Implement Wiki-links Updating

Advances: C2

1. [x] When a file is moved, find all other pages in the vault that contain `[[OldName]]` wiki-links.
2. [x] Update them to point to `[[NewFolder/OldName|OldName]]`.

- [x] Check: `pytest tests/test_garden_folders.py::test_garden_folder_move_updates_links` passes.

## Phase 4: Update Skill Prompt

Advances: C1

1. [x] Edit `src/decafclaw/skills/garden/SKILL.md` to add `Step 2.5: Reorganize Clusters into Folders` instructing the agent to call `vault_reorganize_folders`.

## Phase 5: Verification

- [x] Run all criteria checks.
- [x] Run guards.
