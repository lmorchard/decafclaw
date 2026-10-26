# Notes: Workspace Structural Outlines (v2 Radical Simplification)

- Issue: https://github.com/lmorchard/decafclaw/issues/1135
- Branch: feat-1135-workspace-outlines-v2
- Supersedes: PR #1176
- PR: https://github.com/lmorchard/decafclaw/pull/1180
- Worktree: .claude/worktrees/feat-1135-workspace-outlines-v2
- Base: origin/main at cc9db09

## Decisions
- Pivot away from AST unparsing: taking raw source line at `node.lineno` preserves all Python syntax features (type params `[T]`, metaclasses, decorators, kwargs, one-line defs) without custom syntax reconstruction.
- Drop outline cache: parsing is <2ms. In-memory cache with thread locks was premature optimization and introduced concurrency review issues.
- Simple section lookup: exact case-insensitive match first, unique suffix match fallback. Direct match preserves literal slashes (e.g. `# CI/CD`).
- Boundary detection: multiline headers use `node.body[0].lineno` rather than breaking on colons, correctly preserving typed parameter annotations (`param: int = 1`).
- Child decorator boundary: headers bound at the first body statement's decorator-aware start line, preventing `@property` from leaking into the parent `class` signature.
- AST column offset byte slicing: single-line definitions use byte slicing for `col_offset`, correctly handling multi-byte Unicode identifiers.
- Parenthesized multiline decorators: checks preceding lines for `@` to correctly bound the start line.
- Unified line splitting: `split_physical_lines` with `keepends=True` support unified across `workspace_read`, `workspace_outline`, `workspace_insert`, `workspace_replace_lines`, and `workspace_search`.

## Verification
- `make check`: PASS (npm policy, message types, ruff fmt, ruff check, pyright 0 errors/warnings, check-js, module graph).
- `uv run pytest tests/test_workspace_tools.py`: 152 passed in 4.33s.
- `uv run pytest`: 4,939 passed, 2 skipped in 62.4s.
