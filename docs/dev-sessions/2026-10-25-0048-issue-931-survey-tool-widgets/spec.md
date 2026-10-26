# Spec: Survey existing tools for custom widget presentation opportunities

## Summary
Audit existing built-in tools and capability/skill tools in DecafClaw for rich visual presentation opportunities using DecafClaw's widget framework (`dc-widget-host`). Document opportunities for existing bundled widgets and concrete specifications for proposed new widgets in `docs/tool-widget-opportunities.md`. Implement widget support for high-value low-risk candidate tools (`workspace_edit`, `workspace_diff`, `workspace_list`, `schedule_list`) with complete test coverage.

## Scope & Deliverables
1. **Tool Audit Inventory**:
   - Comprehensive audit of all 63 registered built-in tools across 19 modules in `src/decafclaw/tools/`.
   - Comprehensive audit of skill tools in `src/decafclaw/skills/` (bundled) and `contrib/skills/`.
2. **Analysis & Categorization**:
   - Categorize tools emitting raw text/JSON that benefit from rich widget presentation into:
     - Immediate opportunities leveraging existing bundled widgets (`data_table`, `diff_view`, `code_block`, `json_view`, `markdown_document`, `progress_tracker`).
     - Opportunities benefiting from proposed new widgets.
3. **Proposed Widget Specifications**:
   - Concrete schemas, UI mode support, and data models for proposed new widgets:
     - `key_value_inspector` / `property_card`
     - `file_tree`
     - `multi_tab_view`
     - `timeline_view`
     - `diff_review_card`
4. **Documentation**:
   - Publish `docs/tool-widget-opportunities.md`.
   - Cross-reference from `docs/widgets.md` and `docs/tools.md`.
5. **Implementation of High-Value Low-Risk Candidates**:
   - `workspace_edit`: attach `diff_view` widget with before/after buffers and filename on successful text replacements.
   - `workspace_diff`: attach `diff_view` widget with before/after buffers and compared paths when files differ.
   - `workspace_list`: attach `data_table` widget with Name, Type, Size columns for non-empty directories.
   - `schedule_list`: attach `data_table` widget with Schedule ID, Fire At, Remaining, Reason columns for pending wake turns.
   - All tools maintain strictly unchanged text output contracts for non-web channels and LLM context.
6. **Verification**:
   - Unit tests for newly attached widget requests in `tests/test_workspace_tools.py` and `tests/test_scheduled_wakes.py`.
   - `make check` and `make test` all passing.
