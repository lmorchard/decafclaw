# Implementation Plan

## Phase 1: Registry Schema and Widget Definition
- Create `src/decafclaw/web/static/widgets/json_view/widget.json`.
- Define the schema matching `spec.md`:
  - `modes`: `["inline", "canvas"]`
  - `accepts_input`: `false`
  - `data_schema`: `value` (required), `expand_depth` (optional integer), `path_filter` (optional string).
- Create a stub `src/decafclaw/web/static/widgets/json_view/widget.js`.
- **Advances:** C1, C2, C3
- **Verification:**
  - [x] `uv run pytest tests/test_widgets.py::test_json_view_expand_depth`
  - [x] `uv run pytest tests/test_widgets.py::test_json_view_path_filter`
  - [x] `uv run pytest tests/test_widgets.py::test_json_view_schema_validation`

## Phase 2: Frontend Lit Component
- Implement the UI logic in `src/decafclaw/web/static/widgets/json_view/widget.js`.
- Define `dc-widget-json-view` extending `LitElement`.
- Implement recursive rendering of JSON nodes (`_renderNode`).
- Implement `expand_depth` logic (initial state mapping).
- Implement `path_filter` logic (filtering nodes if path matches).
- Support `inline` vs `canvas` display mode with collapse/expand and "Open in Canvas" actions, similar to `code_block`.
- **Advances:** C1, C2
- **Verification:**
  - [x] `uv run pytest tests/test_widgets.py::test_json_view_expand_depth`
  - [x] `uv run pytest tests/test_widgets.py::test_json_view_path_filter`
