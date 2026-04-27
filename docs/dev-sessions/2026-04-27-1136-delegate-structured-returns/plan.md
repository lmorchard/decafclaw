# Plan

See `spec.md` for decisions.

## Phase 1 — code + tests

- `src/decafclaw/tools/delegate.py`:
  - Module-level `STRUCTURED_OUTPUT_INSTRUCTION` template + `_FENCED_JSON_RE`.
  - `_render_schema_addendum(schema) -> str`.
  - `_parse_structured_output(text) -> tuple[Any | None, str]`.
  - `_run_child_turn(..., return_schema=None)` appends the rendered
    addendum to the child system prompt when the schema is set.
  - `tool_delegate_task(..., return_schema=None)` does the parse +
    `ToolResult` shaping; preserves text-only behavior when
    `return_schema` is None.
  - Tool definition: add `return_schema` parameter (`type: object`).
- `tests/test_delegate.py` — new file (no current test of the
  delegate wrapper). Cover: parse-success, parse-failure, no-schema
  no-op, prose stripping, ToolResult fields.

## Phase 2 — docs

- `docs/delegation.md`: extend with a "Structured returns" section
  (CLI-style example + when to use).

## Phase 3 — squash, push, PR, request Copilot

`Closes #395`. Move project board entry.
