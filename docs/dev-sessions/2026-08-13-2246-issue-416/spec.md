# Widget: json_view — collapsible JSON tree Spec

**Goal:** Collapsible JSON tree for `ToolResult.data` payloads to make large nested ones navigable.

**Source:** https://github.com/lmorchard/decafclaw/issues/416

## Schema

- `modes`: `["inline", "canvas"]`
- `accepts_input`: `false`
- `data_schema`:
  - `value` (any, required) — JSON-serializable payload
  - `expand_depth` (integer, optional, default 2)
  - `path_filter` (string, optional) — initial JSON-path filter

## Notes

- Could become the default rendering for `ToolResult.data` over time, replacing the static fenced block.
- "Copy as JSON path" context-menu action is a nice extra.

## References

- Widget catalog epic: #256

## Verifiable acceptance criteria

- CRITERION: WHEN a `json_view` widget is rendered inline with a payload and `expand_depth: N`, THEN the JSON tree SHALL be rendered with nodes expanded up to depth N and collapsed thereafter.
  CHECK: `pytest tests/test_widgets.py::test_json_view_expand_depth` passes.
  VERIFIED DISCRIMINATING: Fails today because `json_view` widget does not exist.

- CRITERION: WHEN a `json_view` widget is provided with `path_filter`, THEN the tree SHALL highlight or display only the matching sub-tree/path.
  CHECK: `pytest tests/test_widgets.py::test_json_view_path_filter` passes.
  VERIFIED DISCRIMINATING: Fails today because `json_view` widget does not exist.

- CRITERION: WHEN validating a payload against `json_view` data schema, THEN invalid payloads missing the required `value` field SHALL fail schema validation.
  CHECK: `pytest tests/test_widgets.py::test_json_view_schema_validation` passes.
  VERIFIED DISCRIMINATING: Fails today because `json_view` descriptor and schema are not registered.

## Regression guards

- GUARD: `pytest tests/test_widgets.py` passes — preserves existing widget registry and validation behavior.

## Tier: auto-ok
All acceptance criteria are fully checkable via automated tests and no risk-gated paths are touched.
