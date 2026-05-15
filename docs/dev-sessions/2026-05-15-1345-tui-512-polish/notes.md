# Session notes — TUI #512

## Setup findings (2026-05-15 ~13:40)

- Worktree at `.claude/worktrees/tui-512-polish/`, branch `tui-512-polish` tracking `origin/main`.
- `uv sync` + `cd tui && npm install` both clean.
- Baseline: TUI `npm test` 12/12; `npm run typecheck` clean; `make check` clean.

## Scope adjustment during setup

- The original #512 body included a fourth item: "Tighten `CliConfirmResponse.decision` to literal union." PR #493 (manifest typed codegen, merged 2026-05-14) restructured the wire shape; the `decision` field no longer exists. Issue body updated to drop the item; spec.md reflects the smaller scope.
- I'd also just-filed #499 (F3 codegen swap) without realizing PR #493 had already done that work. Closed #499 as already-implemented.

## TODO

- Plan
- Execute
- PR
