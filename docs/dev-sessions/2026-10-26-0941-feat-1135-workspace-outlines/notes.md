# feat-1135-workspace-outlines

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1135
- Worktree: /home/lmorchard/devel/decafclaw/.claude/worktrees/feat-1135-workspace-outlines
- Branch: feat-1135-workspace-outlines
- Base: origin/main at 875f2b7ab70a001000b0f8d67814d57ed8a91dd2
- Status: Complete, draft PR published
- PR: https://github.com/lmorchard/decafclaw/pull/1176

## Baseline

- `make check` passed cleanly (454 files formatted, ruff lint OK, pyright 0 errors/warnings, check-js OK, module graph OK).
- `uv run pytest tests/test_workspace_tools.py -q` passed: 124 passed in 3.14s.

## Decisions

- Add `src/decafclaw/tools/workspace_outlines.py` with AST parsing for Python classes/functions/methods and regex parsing for Markdown headings.
- Provide `tool_workspace_outline(ctx, path, section=None)` in `src/decafclaw/tools/workspace_tools.py` with priority `normal`.
- Enhance `tool_workspace_read` to support `section=None` for targeted section retrieval.
- Invalidate outline cache on content version hash changes and explicit invalidation on workspace write/edit/delete/move/copy tools.
- Explicit fallbacks with line-numbered file content for malformed (SyntaxError) and unsupported file formats.
- Bound outline and section outputs to MAX_OUTLINE_ITEMS (200) and MAX_READ_LINES (200).
- Tool choice eval cases in `evals/tool_choice/core_overlaps.yaml` for outline vs full/targeted reads.

## Changes

- `src/decafclaw/tools/workspace_outlines.py`: Outline parser (Python AST & Markdown headings), caching with content-hash invalidation, section resolver, formatter.
- `src/decafclaw/tools/workspace_tools.py`: Added `tool_workspace_outline`, added `section` support to `tool_workspace_read`, cache invalidation in mutating tools, registered in `WORKSPACE_TOOLS` and `WORKSPACE_TOOL_DEFINITIONS`.
- `src/decafclaw/eval/diagnostics.py`: Registered `workspace_outline` in `READ_TOOL_ARGS`.
- `evals/tool_choice/core_overlaps.yaml`: Added outline vs read disambiguation test cases.
- `docs/tools.md` & `docs/tool-priority.md`: Documented `workspace_outline` and updated `workspace_read`.
- `tests/test_workspace_tools.py`: Added 23 test cases covering classes/functions, signatures, nesting, markdown headings/fences, Unicode, targeted section retrieval, candidate hints, ambiguity, malformed code, unsupported types, empty files, path boundaries, bounded output, cache invalidation, and `workspace_read` section integration.

## Review Follow-up (PR #1176 Review Findings)

- Updated `find_section` in `workspace_outlines.py` to return 3-tuple `(matching_item, ambiguous_matches, known_sections)`.
- Updated `tool_workspace_read` and `tool_workspace_outline` to format `ambig_matches` on ambiguity and `known_sections` on not-found.
- Updated `normalize_lookup` in `workspace_outlines.py` to strip leading `#` and whitespace, supporting `section="# Heading"` or `## Subheading`.
- Updated `tool_workspace_outline` when `not outline_res.items` to show truncation note and set `truncated=True` when `total_lines > MAX_READ_LINES`.
- Updated section truncation message in `tool_workspace_outline` to say `"Use workspace_read with start_line/end_line to read specific ranges."`.
- Added 3 regression tests in `tests/test_workspace_tools.py` verifying no false ambiguity on substring queries, `#` heading lookups, and large no-symbol file truncation.

## Log
