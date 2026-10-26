# Plan: Workspace Structural Outlines (v2 Radical Simplification)

## Phase 1: Engine (`src/decafclaw/tools/workspace_outlines.py`)
- Define `OutlineItem(name, kind, start_line, end_line, heading_line, signature, level, children)`.
- Implement `parse_python_outline(content, path)`:
  - `ast.parse` to extract `ClassDef`, `FunctionDef`, `AsyncFunctionDef`.
  - Slice header directly from `lines[node.lineno - 1]`.
  - Traverse nested functions and methods naturally.
  - Return syntax error details on `SyntaxError`.
- Implement `parse_markdown_outline(content)`:
  - Regex scan matching `vault_show_sections` (`^ {0,3}(#{1,6})\s+(.*?)\s*$`).
  - Calculate content end lines using section levels.
- Implement `format_outline(items, max_items=200)` and `find_section(items, query)`.

## Phase 2: Integration (`src/decafclaw/tools/workspace_tools.py`)
- Add `tool_workspace_outline(ctx, path, section=None)`.
- Update `tool_workspace_read(ctx, path, start_line=None, end_line=None, section=None)` to resolve `section`.
- Register `workspace_outline` in `WORKSPACE_TOOLS` and `WORKSPACE_TOOL_DEFINITIONS`.
- NO cache, NO mutation hooks in `workspace_write`, `workspace_edit`, etc.

## Phase 3: Documentation, Diagnostics & Evals
- Register in `docs/tools.md` and `docs/tool-priority.md`.
- Register `workspace_outline` in `src/decafclaw/eval/diagnostics.py`.
- Add tool-choice evaluation cases in `evals/tool_choice/core_overlaps.yaml`.

## Phase 4: Testing & Verification
- Unit and integration tests in `tests/test_workspace_tools.py`.
- Run `make check` and `uv run pytest tests/test_workspace_tools.py`.
