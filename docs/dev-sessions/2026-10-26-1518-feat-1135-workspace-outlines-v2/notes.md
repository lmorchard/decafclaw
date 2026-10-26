# Notes: Workspace Structural Outlines (v2 Radical Simplification)

- Issue: https://github.com/lmorchard/decafclaw/issues/1135
- Branch: feat-1135-workspace-outlines-v2
- Supersedes: PR #1176
- Worktree: .claude/worktrees/feat-1135-workspace-outlines-v2
- Base: origin/main at cc9db09

## Decisions
- Pivot away from AST unparsing: taking raw source line at `node.lineno` preserves all Python syntax features (type params `[T]`, metaclasses, decorators, kwargs, one-line defs) without custom syntax reconstruction.
- Drop outline cache: parsing is <2ms. In-memory cache with thread locks was premature optimization and introduced concurrency review issues.
- Simple section lookup: exact case-insensitive match first, unique suffix match fallback.
- Reuse heading regex pattern from `vault_show_sections` for Markdown.

## Verification
- `make check`: PASS (npm policy, message types, ruff fmt, ruff check, pyright 0 errors/warnings, check-js, module graph).
- `uv run pytest tests/test_workspace_tools.py`: 146 passed in 4.40s.
- `uv run pytest`: 4,939 passed, 2 skipped in 62.4s.
