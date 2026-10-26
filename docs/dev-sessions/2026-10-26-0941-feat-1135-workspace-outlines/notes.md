# feat-1135-workspace-outlines

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1135
- Worktree: /home/lmorchard/devel/decafclaw/.claude/worktrees/feat-1135-workspace-outlines
- Branch: feat-1135-workspace-outlines
- Base: origin/main at 875f2b7ab70a001000b0f8d67814d57ed8a91dd2
- Status: In progress

## Baseline

- `make check` passed cleanly (454 files formatted, ruff lint OK, pyright 0 errors/warnings, check-js OK, module graph OK).
- `uv run pytest tests/test_workspace_tools.py -q` passed: 124 passed in 3.14s.

## Decisions

- Add `src/decafclaw/tools/workspace_outlines.py` with AST parsing for Python classes/functions/methods and regex parsing for Markdown headings.
- Provide `tool_workspace_outline(ctx, path, section=None)` in `src/decafclaw/tools/workspace_tools.py` with priority `normal`.
- Enhance `tool_workspace_read` to support `section=None` for targeted section retrieval.
- Invalidate outline cache on content version hash changes and explicit invalidation on workspace write/edit/delete/move/copy tools.
- Explicit fallbacks with line-numbered file content for malformed (SyntaxError) and unsupported file formats.
- Bound outline and section outputs to MAX_OUTLINE_ITEMS and MAX_READ_LINES.

## Log
