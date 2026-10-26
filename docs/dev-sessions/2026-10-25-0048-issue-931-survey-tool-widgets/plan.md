# Plan: Survey existing tools for custom widget presentation opportunities

## Phase 1: Audit and Documentation (Draft Checkpoint)
1. Write dev-session files (`spec.md`, `plan.md`, `notes.md`).
2. Draft `docs/tool-widget-opportunities.md`:
   - Section 1: Overview & Widget Integration Contract.
   - Section 2: Complete Tool Inventory (Built-in tools & Skill tools).
   - Section 3: Existing Bundled Widget Opportunities (`data_table`, `diff_view`, `code_block`, `json_view`, `markdown_document`, `progress_tracker`).
   - Section 4: Proposed New Widgets & Technical Specifications (`key_value_inspector`, `file_tree`, `multi_tab_view`, `timeline_view`, `diff_review_card`).
   - Section 5: Implementation Roadmap & Priority Matrix.
3. Update `docs/widgets.md` and `docs/tools.md` to reference `docs/tool-widget-opportunities.md`.
4. Run `make check` to ensure documentation and lint checks pass.
5. Commit Phase 1 checkpoint and publish draft PR via `submit-pr`.

## Phase 2: Implementation of High-Value Low-Risk Candidates
1. `src/decafclaw/tools/workspace_tools.py`:
   - Update `tool_workspace_edit` to return `ToolResult` with `diff_view` `WidgetRequest`.
   - Update `tool_workspace_diff` to return `ToolResult` with `diff_view` `WidgetRequest` when differences exist.
   - Update `tool_workspace_list` to attach `data_table` `WidgetRequest` on `ToolResult` when entries exist.
2. `src/decafclaw/tools/schedule_tools.py`:
   - Update `tool_schedule_list` to attach `data_table` `WidgetRequest` on `ToolResult` when pending wakes exist.
3. Write targeted unit tests:
   - In `tests/test_workspace_tools.py`: tests verifying `diff_view` widget attached to `tool_workspace_edit` and `tool_workspace_diff`, and `data_table` widget attached to `tool_workspace_list`.
   - In `tests/test_scheduled_wakes.py`: test verifying `data_table` widget attached to `tool_schedule_list`.
4. Verify all tests pass (`pytest`) and format/lint gates pass (`make check`).
5. Commit implementation changes.
6. Publish final commits to the draft PR.
