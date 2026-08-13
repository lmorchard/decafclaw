# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/416
**Check files — read-only from Phase 1 onward:**
- `tests/test_widgets.py`

## C1
CRITERION: WHEN a `json_view` widget is rendered inline with a payload and `expand_depth: N`, THEN the JSON tree SHALL be rendered with nodes expanded up to depth N and collapsed thereafter.
CHECK: `uv run pytest tests/test_widgets.py::test_json_view_expand_depth` passes.
AT FREEZE: fails — `AssertionError: assert None is not None`

## C2
CRITERION: WHEN a `json_view` widget is provided with `path_filter`, THEN the tree SHALL highlight or display only the matching sub-tree/path.
CHECK: `uv run pytest tests/test_widgets.py::test_json_view_path_filter` passes.
AT FREEZE: fails — `AssertionError: assert None is not None`

## C3
CRITERION: WHEN validating a payload against `json_view` data schema, THEN invalid payloads missing the required `value` field SHALL fail schema validation.
CHECK: `uv run pytest tests/test_widgets.py::test_json_view_schema_validation` passes.
AT FREEZE: fails — `AssertionError: assert None is not None`

## Guards

- G1: `uv run pytest tests/test_widgets.py` — preserves existing widget registry and validation behavior. Passed at freeze.

## Adjudication

- C1: accepted — the widget doesn't exist, we must create it.
- C2: accepted — the widget doesn't exist.
- C3: accepted — the widget doesn't exist.
- G1: accepted — it is a regression guard.

## Amendments
